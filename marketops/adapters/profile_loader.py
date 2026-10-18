"""Profile 装载：把 ``profiles/<id>/`` 下的 YAML 读成规则对象。

**具体市场的业务规则只进 ``profiles/``，不 hard-code 到通用 engine。**
本模块是那些文件的唯一读者。

**读成什么对象，定义在 ``marketops/domain/profile.py``。** 本模块只做三件事：
找到目录、读 YAML、把读出来的东西校验成规则值。分界线画在「要不要碰外界」上：

    ``grading/engine.py``（今天的 ``application/grading.py``）依赖
    ``domain/profile.py`` 而不是本模块，于是 ``import marketops.application.grading``
    不再连带 import ``yaml`` 与 ``config.py``
    （后者是全仓唯一读 ``os.environ`` 的模块）。「纯判定只在单文件内成立」
    因此变成真的成立 —— 引擎可以在没有磁盘、没有环境变量的情况下被测试。

装载出来的是**两个轴**，区别见 ``domain/profile.py`` 的文件头：
``ProfileBundle.rules``（路线 A/B/C/D/X）与 ``ProfileBundle.account_rules``
（账号准入）**不互相决定**（SOP v1 L188 是两个状态机）。

``grading.yaml`` 的 ``subject: contact-route`` 是这份分工的显式声明：
定级的主语是**一条联系路线**，不是账号，也不是人。装载层会拒绝别的取值 ——
主语写错会让整份规则表读起来仍然合理，只是把等级挂到了错误的对象上。

``unknowns`` 记录的是**引擎为什么还判不了**，不是「业务没说」。SOP 落地后
路线侧已经全部可求值，账号侧的六条 ``x_rules`` 也**全部**编译出了可求值的子句，
因此这份清单今天是**空的**。空不等于「账号侧判得了」：其中五条引用的是
``account.attributes.*`` / ``account.pages.*`` 那类研究产出的引擎侧对应物
（候选事实），它们在账号行上取值为 ``None``，所以账号一律停在 ``discovered``，
而不是被「求不了值的排除规则」排除掉。**装载期与运行期由两个东西回答**，
一个为空不代表另一个为空。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from ..application.validation import one_line_reason
from ..config import Settings
from ..domain.domain_attribution import OfficialDomainVocabulary
from ..domain.enums import (
    AccountStatus,
    BusinessFunction,
    DecisionTask,
    Grade,
    RecordBlockPriority,
    RoleClass,
)
from ..domain.errors import ConfigError
from ..domain.facts import (
    ACCOUNT_QUALIFICATION_FACT_NAMES,
    ROUTE_FACT_NAMES,
    clause_is_well_formed,
)
from ..domain.profile import (
    CLASSIFICATION_AXES,
    CLASSIFICATION_UNKNOWN,
    AccountRule,
    ClassificationAxis,
    ClassificationValue,
    ClassificationVocabulary,
    DeclaredDecisions,
    DeclaredGrade,
    DeclaredGrading,
    DeclaredProfile,
    DeclaredRoleGroup,
    DeclaredRoleMatrix,
    DeclaredScenarioBlock,
    DeclaredScenarioMatrix,
    DeclaredTaskSpec,
    GradingRule,
    ProfileBundle,
    SceneFile,
    SceneFiles,
    as_text,
)
from ..domain.sources import SOURCE_METHODS
from ..domain.temporal import FreshnessPolicy
from ..domain.util import primary_language_subtag

PROFILE_FILE = "profile.yaml"
ROLE_MATRIX_FILE = "role-matrix.yaml"
GRADING_FILE = "grading.yaml"

#: 场景分块声明。**可选** —— 三份必需文件之外的第一份可选声明。
#:
#: 它缺席不是错误：条件 2 要的是有一个**按场景分块**的入口（见
#: ``docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md``），不是逼每一份声明今天就分块。
#: 它存在时则是**必须读通**的：schema 对不上、块里的名字在声明里找不到、
#: 次序不是声明的次序 —— 都是装载失败（见 :func:`_check_scenario_blocks`）。
SCENARIO_MATRIX_FILE = "scenario-matrix.yaml"

#: 三份必需文件的 schema 标识。对不上就是装载失败 —— schema 名是它们唯一的版本契约，
#: 放过去等于把「以后再说」变成一次静默误读。
#:
#: v2 的三处改动，全部是**语义**改动而不是加字段：定级主语的修正（等级挂路线
#: 而不是账号）、A/B 都要 literal 邮箱（旧 B 的定义是错的）、P1–P4 恢复记录块语义
#: （旧 profile 拿它当 URL 路径分级）。改语义就要改版本号，否则一份 v1 文件会被
#: 静默地按 v2 读进去。
SOP_PROFILE_SCHEMA = "marketops/profile.v2"
SOP_GRADING_SCHEMA = "marketops/grading.v2"
SOP_ROLE_MATRIX_SCHEMA = "marketops/role-matrix.v2"
#: 场景分块声明的 schema。v1 是新的一族，不是上面三份里任何一份的续版 ——
#: 它这一版就是「四格收窄」那句话，没有旧文件会被它按新读法读进来。
SOP_SCENARIO_MATRIX_SCHEMA = "marketops/scenario-matrix.v1"

#: 唯一的合法定级主语。
CONTACT_ROUTE_SUBJECT = "contact-route"

#: 排除规则允许把账号置为的状态。``eligible`` / ``researched`` / ``archived``
#: 都不是「排除」的结论，写在这里说明作者想要的是别的东西。
_EXCLUSION_STATUSES: frozenset[AccountStatus] = frozenset(
    {AccountStatus.IRRELEVANT, AccountStatus.HOLD, AccountStatus.DUPLICATE}
)

#: 表达式方言里对账号事实的引用形如 ``account.target_match.score``。
#: 这里只做**引用提取**，不求值 —— 求值器不在本模块。
_FACT_REF_RE = re.compile(r"\baccount(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


#: 装载一份声明时用来**点名**的那个串：从盘上读时是一个 ``Path``，从一个 Scene
#: 快照的字节里读时是快照里的相对路径。
#:
#: 两者都不是「文件在哪」这个问题的完整答案 —— 字节那份根本不在任何目录里。
#: 它们要回答的是另一个问题，而那个问题两条路都得答：**是哪一份文件**写坏了。
#: 所以下面这几个收 ``_Where`` 的地方，报错里逐字带上它，而且只有它。
_Where = str | Path


def _load_yaml(path: Path) -> dict:
    """读**盘上**一个 YAML 文件。见 :func:`_parse_yaml`：解析那一半是共用的。"""
    if not path.exists():
        raise ConfigError(f"profile 文件缺失：{path}")
    return _parse_yaml(path.read_bytes(), path)


def _parse_yaml(content: bytes, where: _Where) -> dict:
    """一份声明的**字节** → 映射。``where`` 只用于报错时点名。

    与 :func:`_load_yaml` 分开，是因为两条装载路线的字节来源不同（盘上的目录 /
    一个被钉住的 Scene 版本），而「字节 → 映射」这一步在两条路上必须逐字相同 ——
    各写一遍的下场是一边收了非映射的顶层、另一边没有。
    """
    try:
        data = yaml.safe_load(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"profile 文件不是合法的 UTF-8：{where}：{exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"profile 文件不是合法 YAML：{where}：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"profile 文件顶层必须是映射：{where}")
    return data


def _require_schema(document: dict, expected: str, where: _Where) -> None:
    actual = document.get("schema")
    if actual != expected:
        raise ConfigError(
            f"{where} 的 schema 是 {actual!r}，本 loader 只支持 {expected!r}。\n"
            "  不认识的版本要显式失败，否则一次误读会静默产出一份看起来正常的判定规则。"
        )


def _as_list(value: object, field_name: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} 必须是列表，实际是 {type(value).__name__}")
    return value


#: 装载器内部的私有拼写。实现只有一份，在 ``domain/profile.py`` ——
#: 「空值读成空串」这个口径如果漂移，会以「某个字段在某个文件里是 ``None``、
#: 在另一个里是 ``""``」的形式出现，而那种差异在报告上看不出来。
_as_text = as_text


def _as_enum(enum: type, value: object, field_name: str):
    raw = _as_text(value)
    try:
        return enum(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum)
        raise ConfigError(f"{field_name} 非法：{raw!r}。允许取值：{allowed}") from exc


# 规则值（``GradingRule`` / ``AccountRule`` / ``Declared*`` / ``ProfileBundle``）
# 定义在 ``marketops/domain/profile.py``，上面 import 进来只为**转发**：
# 本模块是那些类型的公开入口，调用方不必知道它们住在哪一层。
#
# 这里**不重复定义**它们。两份真实定义的下场不是「有一份是旧的」，而是
# 「engine 用一份、装载器用另一份」，而两者在 ``dataclass(frozen=True)`` 下
# 连 ``==`` 都还是 True —— 类型检查会过、测试会过，只有 ``isinstance`` 会假。


# --- 装载 -------------------------------------------------------------------


def read_scene_files(directory: Path) -> SceneFiles:
    """profile 目录里**每一个文件**的字节，读成一个内容快照。

    这份快照就是「当时那套规则到底长什么样」。它的 ``version`` 是 ``get_scene``
    报的「生效的策略/指引版本」，也是作业钉在 WorkItem 上的那一个
    （§6「Running tasks retain their pinned guidance」）。

    ## 读出来的是内容，不只是摘要 —— 这是本次改动的全部要点

    从前这里算完摘要就把字节丢掉了，于是盘上只剩一个版本号：谁也没法回答「那次
    派发用的指引是什么」。而 ``GuidancePin`` 只存 ``(work_item_id, scene_version,
    pinned_at)``，``get_assignment`` 因此只能回一个哈希 —— 一个**说得出钉过、
    说不出钉的是什么**的回执。摘要不是内容：它对不上时能告诉你「变了」，对得上时
    对「是什么」一个字也没说。

    现在同一次读既给出字节也给出摘要，两者**来自同一次遍历**。分成两次读会有
    TOCTOU：版本描述的是一批字节，内容却是另一批，而两份都「没坏」。

    ## 覆盖面是**目录**，不是「那三个 YAML」

    今天这个目录里只有三个文件，但 ScenePack 还会带**程序性指引**
    （``docs/SCENE_EVOLUTION.md`` 的 ``skill/SKILL.md``），而那种文件的名字今天
    还不存在。所以这里走目录，不走一份写死的文件清单 —— 清单的下场是新加的指引
    文件**不参与版本**，于是「指引换了、版本没动」，正是这套机制唯一要防的失败。

    同理**没有排除模式**（``.DS_Store`` / ``*~`` / ``__pycache__``）。排除表的代价
    是某天排除掉一个真的指引文件，而那一天没有任何信号；不排除的代价只是版本多动
    几次（一次假的「版本变了」）。前者不可见，后者可见 —— 选后者。

    ## 散的是字节，不是读出来的规则值

    YAML 里多个 BOM、缩进从两格改成四格，规则值逐位不变而版本会换。这是选「字节」
    的已知代价，方向安全：多一次假的「版本变了」，而不是少一次真的。反过来
    「解码后的值」也不是更好的口径 —— 那时版本描述的是一份被解释过的东西，
    而真正被部署的是盘上的字节。

    符号链接跟着走（``is_file()`` 会跟），与 ``_load_yaml`` 一致：它读的是同一个
    路径。快照要装的是**这个装载器真的读到的那几个字节**，而不是某种理想化的
    目录树。

    目录遍历顺序由文件系统给，不保证稳定，所以这里按相对 posix 路径排序之后再
    交给 ``SceneFiles`` —— 不排序的版本会在没有任何改动时变来变去。
    ``SceneFiles.version`` 只负责散列，排序是读的人的责任。

    ## 文件清单不可能为空

    ``load_profile`` 是在三个 ``_load_yaml`` 都读成功之后才调本函数的，所以这个
    目录里至少有三个普通文件。空清单对应的「空目录摘要」因此没有输入能走到；
    ``SceneFiles`` 自己会拒绝空 entries，这条拒绝在这里是一道走不到的防线。
    """
    paths = sorted(
        (path for path in directory.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    return SceneFiles(
        entries=tuple(
            SceneFile(
                path=path.relative_to(directory).as_posix(),
                content=path.read_bytes(),
            )
            for path in paths
        )
    )


def _require_declared_profile_id_matches_workspace(
    declared: DeclaredProfile, *, settings: Settings
) -> None:
    """``profile.yaml`` 的 ``name`` 必须**逐字**等于 ``settings.profile``。

    ## 为什么这条拒绝是必须的，而不是一条风格检查

    ``profile.yaml`` 的 ``name`` 是 SOP 自己给这套规则起的名字，
    ``settings.profile`` 是这个部署**站在哪个工作区**（目录名 /
    ``MARKETOPS_PROFILE``）。两者一旦不同，同一个部署就有两个「Scene」的说法，
    而它们各自都有读者：

    * 授权那一层按 ``settings.profile`` 判（``_require_scene`` 拒绝别的值）；
    * 研究产出的血统格（``Evidence.workspace``）会按 ``bundle.profile_id`` 写。

    于是报告上的一行证据写着 ``name``，而读它的人按 ``settings.profile`` 授权 ——
    两个名字都像 Scene，谁也没有说谎，而「这一行属于谁」这件事在盘上有了两个答案。
    这正是本仓要消灭的那种形状（同一个概念两个写法），所以这里**拒绝**，不挑一个
    继续跑。

    ## 为什么不是「以 ``settings.profile`` 为准、把声明忽略掉」

    忽略声明等于把一次配置错误变成一次静默的改名：盘上的规则文件说自己是 ``bar``，
    而每一行产出写着 ``foo``。要改的是那两份中的一份 —— 要么 ``MARKETOPS_PROFILE``
    指到 ``profiles/bar``，要么 ``profile.yaml`` 的 ``name`` 改成 ``foo``。

    ## 它**不**保证的事

    它保证两个名字相等，**不**保证这个目录里装的规则与 ``name`` 所声称的那套规则
    是同一次交付 —— 那件事的载体是 ``scene_version``（内容的摘要），不是名字。
    """
    if declared.name != settings.profile:
        raise ConfigError(
            f"profile.yaml 声明的 name={declared.name!r} 与本部署的工作区 "
            f"{settings.profile!r} 不同。\n"
            "工作区（Scene）在这套系统里只有一个名字：授权按它判、研究产出的血统格"
            "（Evidence.workspace）按它写。两个写法会让同一行证据在一个地方叫一个名字、"
            "在另一个地方叫另一个名字。\n"
            "改其中一处：把 MARKETOPS_PROFILE（或 profiles 目录名）指到 "
            f"{declared.name!r}，或者把 profile.yaml 的 name 改成 {settings.profile!r}。"
        )


def _where(name: str, *, directory: Path | None) -> _Where:
    """三份声明的名字 → 报错时点名的那个串。

    从盘上装载时是那个目录下的完整路径（处置的第一步是去看那个文件）；从一个
    Scene 快照装载时就是快照里的相对路径 —— 那时候没有目录可说，**编一个出来
    比一个名字更坏**：它会把读者指到一台可能根本没有这份 profile 的机器上。
    """
    return name if directory is None else directory / name


def _declaration(documents: Mapping[str, dict], name: str, *, directory: Path | None) -> dict:
    """从一批声明里取一份**必需**声明；不在就是装载失败。

    这条拒绝只在「从一个 Scene 快照装载」那条路上有输入：从盘上装载时，
    :func:`_load_yaml` 已经在那份文件缺失时就红过了。
    """
    document = documents.get(name)
    if document is None:
        raise ConfigError(
            f"{_where(name, directory=directory)} 不在这一批 Scene 字节里。\n"
            "  三份必需声明（profile.yaml / role-matrix.yaml / grading.yaml）是装载的"
            "锚点 —— 少一份就没有规则可读。走到这里说明这一批字节不来自一个完整的"
            "profile 目录，而不是「那一份文件是空的」。"
        )
    return document


def _bundle_from_documents(
    documents: Mapping[str, dict],
    *,
    scene_files: SceneFiles,
    settings: Settings,
    directory: Path | None,
) -> ProfileBundle:
    """三份必需声明 + 可选场景声明 → 一个 ``ProfileBundle``。

    **这是装载的唯一下半段。** :func:`load_profile`（从盘上的目录读字节）与
    :func:`load_profile_from_scene_files`（从一个被钉住的 Scene 版本读字节）在
    这里合成一条 —— 校验序列因此逐条相同，而不是两份会漂移的拷贝。

    另写一份的代价不是重复：某天在这一条路上补了一条判据（比如场景块与声明的
    对账），另一条路上没有 —— 于是同一批字节经两条路装载出两个 ``ProfileBundle``，
    而两边都「没坏」，报告上没有任何一处会红。

    ``directory`` 只用于报错时点名，见 :func:`_where`；它不是本函数的输入，
    更不是规则的一部分（``ProfileBundle`` 里没有目录，见 ``domain/profile.py``）。
    """
    profile_data = _declaration(documents, PROFILE_FILE, directory=directory)
    role_data = _declaration(documents, ROLE_MATRIX_FILE, directory=directory)
    grading_data = _declaration(documents, GRADING_FILE, directory=directory)

    _require_schema(profile_data, SOP_PROFILE_SCHEMA, _where(PROFILE_FILE, directory=directory))
    _require_schema(
        role_data, SOP_ROLE_MATRIX_SCHEMA, _where(ROLE_MATRIX_FILE, directory=directory)
    )
    _require_schema(
        grading_data, SOP_GRADING_SCHEMA, _where(GRADING_FILE, directory=directory)
    )

    declared_profile = _load_declared_profile(profile_data)
    _require_declared_profile_id_matches_workspace(declared_profile, settings=settings)
    role_matrix = _load_declared_role_matrix(role_data)
    declared = _load_declared_grading(grading_data)
    vocabulary = _compile_role_vocabulary(role_matrix)

    # 场景分块：可选文件，但它一旦在，就必须与上面那两块**对得上**。
    #
    # 这一条校验放在装载期，理由与 ``profile.exclusions`` 的 ``declared_ids`` 逐字
    # 相同：一个拼错的档位名在编译期只在**那个场景真的来临时**才红（否则永不生效），
    # 而一份声明里最不容易被读到的恰恰是那几个还没被用过的块。
    scenario_data = documents.get(SCENARIO_MATRIX_FILE)
    scenario_matrix = (
        _load_declared_scenario_matrix(
            scenario_data, where=_where(SCENARIO_MATRIX_FILE, directory=directory)
        )
        if scenario_data is not None
        else None
    )
    if scenario_matrix is not None:
        _check_scenario_blocks(scenario_matrix, declared=declared_profile, roles=role_matrix)

    return ProfileBundle(
        profile_id=declared_profile.name or settings.profile,
        status=profile_data.get("status"),
        scene_version=scene_files.version,
        scene_files=scene_files,
        rules=_engine_rules(declared),
        role_terms=vocabulary.terms,
        unknowns=_unknowns(declared),
        declared=declared,
        role_matrix=role_matrix,
        profile=declared_profile,
        scenario_matrix=scenario_matrix,
        decisions=_load_declared_decisions(profile_data),
        freshness=_load_declared_freshness(profile_data),
        account_rules=declared.x_rules,
        role_classes=vocabulary.classes,
        business_functions=vocabulary.functions,
        excluded_terms=vocabulary.excluded,
    )


def load_profile_from_scene_files(scene_files: SceneFiles, *, settings: Settings) -> ProfileBundle:
    """从**一批 Scene 快照的字节**装载 bundle。

    ## 它补的是哪一个洞

    :func:`load_profile` 读的是**盘上此刻**的目录。而「这件工作按哪一份规则在跑」
    那个版本号指的是一批**已经冻住的字节**（``scene_snapshot`` 表；读回来就是
    ``SceneFiles``）—— 盘上的目录之后被改过时，两者就是两份规则。

    判定与结算那几条路径要的是**前者**：§6 逐字 Running tasks retain their pinned
    guidance，而「钉住」如果只钉版本号、执行的时候仍然读盘上那一份，那句话在报告上
    照样读得通 —— 只是它说的是另一件事。

    ## 它与 ``load_profile`` 走**同一条**校验序列

    两条路在 :func:`_bundle_from_documents` 合成一条，见那里的 docstring。

    ## 报错时点名的是快照里的相对路径

    这里没有目录可说 —— 这些字节不来自任何目录，可能是另一台机器、另一个时刻的
    一个 ``profiles/<id>/``。所以报错里给的是 ``SceneFiles.entries`` 里的 ``path``
    （``grading.yaml`` 这种），而不是一个拼给读者看的绝对路径。

    ## ``settings`` 仍然是必需的

    工作区那一条判据（:func:`_require_declared_profile_id_matches_workspace`）
    比对的是**声明的名字**与**本部署站在哪个工作区**，而后者与字节的来源无关：
    授权按 ``settings.profile`` 判，产出的血统格按 ``bundle.profile_id`` 写。
    拿一批别的工作区的字节当这一份用，正该在这里被拒。
    """
    entries = scene_files.as_mapping()
    documents: dict[str, dict] = {}
    for name in (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE, SCENARIO_MATRIX_FILE):
        content = entries.get(name)
        if content is None:
            # 可选的那一份可以不在；必需的那三份由 ``_declaration`` 拒绝，那句拒绝
            # 说得清是哪一份 —— 在这里提前拼一句「缺少 profile.yaml」会把
            # 「这一批字节不完整」与「这一份声明写坏了」压成同一句话。
            continue
        documents[name] = _parse_yaml(content, name)

    return _bundle_from_documents(
        documents, scene_files=scene_files, settings=settings, directory=None
    )


def load_profile(settings: Settings) -> ProfileBundle:
    """装载 ``settings.profile`` 对应的 profile。

    ## 一份 profile 的 id 与它所在的工作区**必须同名**

    底下那一条（:func:`_require_declared_profile_id_matches_workspace`）是本模块
    唯一一处把「声明的 profile 名」与「部署的工作区」对起来的地方。它存在的理由
    不是整洁：``settings.profile`` 是**授权**那一层的取值（``marketops/security/
    workspace.py`` 的 ``RoleGuard`` 按它判、MCP 的 ``_require_scene`` 按它拒），
    而 :attr:`~marketops.domain.profile.ProfileBundle.profile_id` 是写到研究产出
    上的**血统**取值（``Evidence.workspace`` / ``ContactRoute.workspace`` /
    ``RouteOpportunity.workspace``）。两个取值要是能不同，同一批产出上就会有两个
    都叫「Scene」的答案：一行证据写着 ``bar``，而读者按 ``foo`` 去授权。
    在此之前它们只是**碰巧**相同 —— 盘上唯一那份 profile 恰好声明了与目录同名的
    ``name``。碰巧不是约束：这条判据把它变成约束。
    """
    directory = settings.profile_dir()
    if not directory.is_dir():
        raise ConfigError(
            f"profile 目录不存在：{directory}\n"
            "业务规则只从 profiles/<profile_id>/ 读取；请检查 MARKETOPS_PROFILE "
            "与 MARKETOPS_PROFILES_DIR。"
        )

    documents = {
        name: _load_yaml(directory / name)
        for name in (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE)
    }
    scenario_path = directory / SCENARIO_MATRIX_FILE
    if scenario_path.exists():
        documents[SCENARIO_MATRIX_FILE] = _load_yaml(scenario_path)

    # 一份装载不出来的 profile 不该**有版本**：有版本就意味着「这套规则被生效过」，
    # 而它一次都没被读成规则。下面那一条共用的下半段跑完之前不会构造出
    # ``ProfileBundle`` —— 版本写在那上面，所以崩在半路的装载不会漏出一个版本号。
    # （那次遍历确实先发生了，但它的结果只活在这次调用里，没有任何出口。）
    #
    # 快照与版本取自**同一次遍历**（``read_scene_files`` 读了什么，``.version``
    # 就散什么），所以「版本说的是这批字节」这句话在结构上成立，而不是靠两次读
    # 恰好读到同一份。
    return _bundle_from_documents(
        documents,
        scene_files=read_scene_files(directory),
        settings=settings,
        directory=directory,
    )


def _load_declared_profile(data: dict) -> DeclaredProfile:
    name = _as_text(data.get("name") or data.get("profile_id"))
    if not name:
        raise ConfigError("profile.yaml 缺少 name —— profile id 是装载的锚点，不能为空")

    exclusions = _as_list(data.get("exclusions"), "profile.exclusions")
    if not exclusions:
        raise ConfigError(
            "profile.yaml 的 exclusions 必须是非空列表。\n"
            "  空列表不是「没有排除项」，而是「所有账号都能进候选集」—— 那是一次静默的"
            "规则放宽，必须写出来而不是留空。"
        )
    for item in exclusions:
        if not isinstance(item, dict) or not _as_text(item.get("id")):
            raise ConfigError(f"profile.exclusions 的条目需要 id：{item!r}")
    #: 已声明的排除项 id，保序去重。分类词表的 ``excludes`` 回指的就是这一份 ——
    #: 校验放在装载期而不是匹配期：一个拼错的 id 在匹配期不会报错，它只是永不生效。
    declared_ids = tuple(dict.fromkeys(_as_text(item["id"]) for item in exclusions))

    compliance = data.get("compliance")
    if not isinstance(compliance, dict):
        raise ConfigError("profile.yaml 缺少 compliance 映射 —— 抓取行为边界不能缺省")

    hints_raw = data.get("page_discovery_hints") or {}
    if not isinstance(hints_raw, dict):
        raise ConfigError("profile.yaml 的 page_discovery_hints 必须是映射（优先级 → URL glob 列表）")
    hints: dict[str, tuple[str, ...]] = {}
    for key, value in hints_raw.items():
        globs = tuple(_as_text(item) for item in _as_list(value, f"page_discovery_hints.{key}"))
        globs = tuple(item for item in globs if item)
        if not globs:
            raise ConfigError(f"page_discovery_hints.{key} 是空的 —— 空优先级不会抓到任何页面")
        hints[_as_text(key)] = globs

    return DeclaredProfile(
        schema=_as_text(data.get("schema")),
        name=name,
        display_name=_as_text(data.get("display_name")),
        country=_as_text(data.get("country")),
        exclusions=tuple(exclusions),
        compliance=compliance,
        page_discovery_hints=hints,
        search_max_queries=_as_search_max_queries(data),
        search_locales=_as_search_locales(data),
        fetch_max_documents=_as_fetch_max_documents(data),
        classification=_load_classification(data, declared_exclusion_ids=declared_ids),
        official_domain=_load_official_domain(data),
        raw=data,
    )


def _looks_like_a_bare_host(value: str) -> str:
    """这一串是不是**裸主机名**。不是则返回一句为什么，是则返回空串。

    这里挡的不是拼写洁癖，是**一个永不生效的条目**：名单的匹配对象是
    ``attribute_official_domain`` 拿到的那台主机（裸的，无 scheme、无路径）。
    写着 ``https://facebook.com`` 或 ``facebook.com/`` 的条目**一条也匹配不上**，
    而它看起来与一条正常的声明逐字相像 —— 读的人会以为那台主机已经被排掉了。

    同理拒掉前导点（``.facebook.com`` 永远不会等于 ``facebook.com``，也不会以
    ``.`` 加它结尾）、空白、以及没有点的单标签（``facebook`` 挡不住
    ``facebook.com``）。
    """
    if value != value.strip():
        return "首尾有空白"
    if "://" in value:
        return "带了 scheme —— 匹配的对象是裸主机名，带 scheme 的条目永远匹配不上"
    if any(char in value for char in "/?#@:"):
        return "带了路径、端口或用户信息 —— 匹配的对象是裸主机名"
    if value.startswith(".") or value.endswith("."):
        return "首尾有点 —— 前导点会让它永远匹配不上，尾点不是主机的写法"
    if ".." in value:
        return "有空标签（连续两个点）"
    labels = value.split(".")
    if len(labels) < 2:
        return "是单标签，不是主机名 —— 它挡不住任何一台带后缀的主机"
    for label in labels:
        if not label or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label):
            return f"标签 {label!r} 不是合法的主机标签（只收小写字母、数字与中划线）"
    return ""


def _load_official_domain(data: dict) -> OfficialDomainVocabulary | None:
    """``profile.yaml`` 的 ``official_domain`` 块 → 官网判定的参数集。

    与 :func:`_load_classification` 同一条纪律：这里校验的每一条都挡着一个具体的、
    **静默的**失败形态。

    * **整块缺失 ⇒ ``None``。** 一份 profile 可以先只有规则、后有官网判定，
      而那不是错误（同 ``classification``）。
    * **写了却什么都没有 ⇒ 报错。** 一个空的 ``official_domain: {}`` 读起来像
      「官网判定已经声明过了」，跑起来什么参数都不给 —— 两条读起来不一样，
      做出来的事一样。
    * **第三方主机名必须是裸主机。** 见 :func:`_looks_like_a_bare_host`：带
      scheme 或路径的条目**永远匹配不上**，而它看起来与正常声明逐字相像。
    * **缺省词必须是判据真的能碰到的写法。** 词表切分后统一折成小写，所以一条
      ``The`` 永远不等值于 ``the``；判据碰不到它，而名单里挂着它会让人以为
      那个词已经被过滤掉了。这里只收纯小写字母与数字。
    * **两个界必须是正整数。** ``0`` 会让判据恒成立（``min_name_matches=0``
      意味着任何主机都被解释）—— 那不是「宽松」，是**判据不成立**。
    """
    block = data.get("official_domain")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ConfigError(f"profile.yaml 的 official_domain 必须是映射，实际是 {block!r}")

    raw_hosts = _as_list(block.get("third_party_hosts", []), "official_domain.third_party_hosts")
    raw_stopwords = _as_list(block.get("name_stopwords", []), "official_domain.name_stopwords")
    thresholds_present = any(key in block for key in ("min_name_matches", "min_word_length"))
    if not raw_hosts and not raw_stopwords and not thresholds_present:
        raise ConfigError(
            "profile.yaml 的 official_domain 块是空的。\n"
            "  空块读起来像「官网判定已经声明过了」，跑起来一个参数都不给 ——"
            "  两条不一样，做出来的事一样。要么写进参数，要么整块别写。"
        )

    hosts: list[str] = []
    for item in raw_hosts:
        host = _as_text(item).lower()
        if not host:
            raise ConfigError(
                f"official_domain.third_party_hosts 里有空条目：{raw_hosts!r}"
            )
        why = _looks_like_a_bare_host(host)
        if why:
            raise ConfigError(
                f"official_domain.third_party_hosts 的条目 {item!r} 不是裸主机名：{why}。\n"
                "  匹配的对象是 attribute_official_domain 拿到的那台主机，而它已经被"
                "  折成裸主机 —— 一个带 scheme 的条目永远匹配不上，"
                "  但它在名单里看起来与一条正常声明逐字相像。"
            )
        hosts.append(host)

    stopwords: list[str] = []
    for item in raw_stopwords:
        word = _as_text(item)
        if not word:
            raise ConfigError(f"official_domain.name_stopwords 里有空条目：{raw_stopwords!r}")
        if not re.fullmatch(r"[a-z0-9]+", word):
            raise ConfigError(
                f"official_domain.name_stopwords 的条目 {item!r} 不是判据碰得到的写法"
                "（只收纯小写字母与数字）。\n"
                "  词表切分之后统一折成小写，所以写着 'The' 的那一条永远等值不上 'the' ——"
                "  判据碰不到它，而名单里挂着它会让人以为那个词已经被过滤掉了。"
            )
        stopwords.append(word)

    min_matches = _as_positive_int(
        block.get("min_name_matches", 2), "official_domain.min_name_matches"
    )
    min_length = _as_positive_int(
        block.get("min_word_length", 4), "official_domain.min_word_length"
    )

    return OfficialDomainVocabulary(
        third_party_hosts=tuple(dict.fromkeys(hosts)),
        name_stopwords=tuple(dict.fromkeys(stopwords)),
        min_name_matches=min_matches,
        min_word_length=min_length,
    )


def _as_positive_int(value: object, field_name: str) -> int:
    """正整数，且**不接受** ``"4"`` 这种字符串。

    YAML 会把 ``4`` 读成 int、``"4"`` 读成 str，而 ``bool`` 是 ``int`` 的子类 ——
    所以 ``False`` 会被朴素写法悄悄收成 ``0``。这两条都在这里挡住：判据的界被读成
    一个意外的值时，判据的结论会变，而报告上它只是「数字不一样」。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"profile.yaml 的 {field_name} 必须是整数，实际是 {value!r}（{type(value).__name__}）"
        )
    if value < 1:
        raise ConfigError(
            f"profile.yaml 的 {field_name} 必须 ≥ 1，实际是 {value}。\n"
            "  0 不是「宽松」，是判据不成立：一个恒成立的门没有挡住任何东西。"
        )
    return value


def _load_classification(
    data: dict, *, declared_exclusion_ids: tuple[str, ...]
) -> ClassificationVocabulary | None:
    """``profile.yaml`` 的 ``classification`` 块 → 分类词表。

    这里校验的每一条都挡着一个具体的、**静默的**失败形态：

    * **轴名必须是 :data:`CLASSIFICATION_AXES` 里的那四条。** 轴名同时是事实名
      （``marketops/domain/facts.py``），而那条事实名到引擎的路径上没有任何映射表。
      写错一个轴名的后果不是报错 —— 是那条轴的读数**永远不会被任何规则看见**，
      而报告上它与「这一页没命中」逐字相同。
    * **取值不得叫 ``unknown``。** 那个字符串是「没有结论」的保留写法。把它当成
      一个取值，会让「查出来是 unknown」与「没查出来」在数据上重新合流 ——
      而这一层存在的全部理由就是不让它们合流。
    * **每个取值至少要有一条判据**（``phrases`` 或 ``host_suffixes``）。一条都没有
      的取值永远匹配不上，而它挂在词表里会让读的人以为这一轴查过了。
    * **``excludes`` 必须是已声明的排除项 id。** 与 ``grading.yaml`` 引用事实名
      同一条纪律：一个拼错的排除项 id 不会报错，它只是**永不生效** ——
      而这条排除本来是要把一个账号挡在外面的。
    * **同一轴上取值不得重名**（YAML 的重复键会被 PyYAML 静默吞掉最后一个，
      所以这里查的是解析**之后**的列表）。

    整块缺失返回 ``None``：一份 profile 可以先只有规则、后有分类，而那不是错误。
    写了 ``classification:`` 却一条轴都没有，那是一份写坏了的声明。
    """
    block = data.get("classification")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ConfigError(f"profile.yaml 的 classification 必须是映射，实际是 {block!r}")

    axes_raw = block.get("axes")
    if not isinstance(axes_raw, dict) or not axes_raw:
        raise ConfigError(
            "profile.yaml 的 classification 要么整个不写，要么带一个非空的 axes 映射"
            f"（轴名 → 取值列表），实际是 {axes_raw!r}"
        )

    axes: list[ClassificationAxis] = []
    for axis_name, values_raw in axes_raw.items():
        name = _as_text(axis_name)
        if name not in CLASSIFICATION_AXES:
            raise ConfigError(
                f"profile.yaml 的 classification.axes 里有未知的轴 {name!r}。\n"
                f"  已知的轴就是事实名本身：{'、'.join(CLASSIFICATION_AXES)}。\n"
                "  轴名写错的后果不是报错，是那条轴的读数永远不会被任何规则看见 ——"
                "  而报告上它与「这一页没命中」逐字相同。"
            )
        entries = _as_list(values_raw, f"classification.axes.{name}")
        if not entries:
            raise ConfigError(
                f"classification.axes.{name} 是空的。\n"
                "  空取值表不是「这条轴不用判」，而是「这条轴永远判不出来」——"
                "  两条读起来不一样，做出来的事一样：那一轴的规则会永远停在 unchecked。"
                "  要么给出取值，要么整条轴别写。"
            )
        values: list[ClassificationValue] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigError(f"classification.axes.{name} 的条目必须是映射：{entry!r}")
            value = _as_text(entry.get("value"))
            if not value:
                raise ConfigError(f"classification.axes.{name} 的条目缺少 value：{entry!r}")
            if value == CLASSIFICATION_UNKNOWN:
                raise ConfigError(
                    f"classification.axes.{name} 里的取值不得叫 {CLASSIFICATION_UNKNOWN!r}。\n"
                    "  那一个字符串是「没有结论」的保留写法：查不出取值、以及同一轴上"
                    "  命中了两个不同取值，都写成它。把它也当成一个取值，"
                    "  「查出来是 unknown」与「没查出来」就再也分不开了。"
                )
            if value in seen:
                raise ConfigError(
                    f"classification.axes.{name} 里的取值 {value!r} 出现了两次。\n"
                    "  YAML 的重复键会被静默吞掉最后一个，所以这里查的是解析之后的列表 ——"
                    "  一份有两个同值条目的词表，读起来像两条判据，跑起来只有一条。"
                )
            seen.add(value)

            phrases = tuple(
                item for item in (t.strip() for t in _as_list(entry.get("phrases", []), "phrases")) if item
            )
            suffixes = tuple(
                item
                for item in (t.strip() for t in _as_list(entry.get("host_suffixes", []), "host_suffixes"))
                if item
            )
            excludes = tuple(
                dict.fromkeys(
                    item
                    for item in (t.strip() for t in _as_list(entry.get("excludes", []), "excludes"))
                    if item
                )
            )
            if not phrases and not suffixes:
                raise ConfigError(
                    f"classification.axes.{name} 的取值 {value!r} 没有任何判据"
                    "（phrases 与 host_suffixes 都是空的）。\n"
                    "  一条永远匹配不上的取值挂在词表里，会让读的人以为这条轴查过了。"
                )
            unknown_exclusions = [
                item for item in excludes if item not in declared_exclusion_ids
            ]
            if unknown_exclusions:
                raise ConfigError(
                    f"classification.axes.{name} 的取值 {value!r} 回指了未声明的排除项 "
                    f"{unknown_exclusions!r}。\n"
                    f"  profile.exclusions 里声明过的是：{'、'.join(declared_exclusion_ids)}。\n"
                    "  一个拼错的排除项 id 不会报错，它只是永不生效 ——"
                    "  而这条排除本来是要把一个账号挡在外面的。"
                )
            values.append(
                ClassificationValue(
                    value=value,
                    phrases=phrases,
                    host_suffixes=suffixes,
                    excludes=excludes,
                )
            )
        axes.append(ClassificationAxis(axis=name, values=tuple(values)))

    return ClassificationVocabulary(axes=tuple(axes))


def _load_declared_decisions(data: dict) -> DeclaredDecisions | None:
    """``profile.yaml`` 的 ``decisions`` 块 → task → 题面声明。

    **键集的对齐不在这里做。** 一道题的选项必须逐字等于 profile 自己的一个闭集
    取值（页面档位 / 责任关系 / business function），而那些闭集在别处 ——
    ``application/decision_specs.py`` 才是同时看得见题面与闭集的那一层。本函数只
    保证 YAML 读出来的形状是对的，而形状错了在这里就红：再往下走，一个空的
    描述会以「题面通过了校验、模型答了一个闭集外的取值」的样子出现在报告上。

    整块缺失返回 ``None``：一个 profile 可以不做决策，而那不是错误。**部分缺失
    不是**：写了 ``decisions:`` 却一个 task 都没有，那是一份写坏了的声明。
    """
    block = data.get("decisions")
    if block is None:
        return None
    if not isinstance(block, dict) or not block:
        raise ConfigError(
            "profile.yaml 的 decisions 要么整个不写，要么是一个非空映射"
            f"（task → 题面），实际是 {block!r}"
        )

    tasks: dict[DecisionTask, DeclaredTaskSpec] = {}
    for raw_name, raw_spec in block.items():
        task = _as_enum(DecisionTask, raw_name, "profile.decisions 的键")
        if task in tasks:
            raise ConfigError(f"profile.decisions 里 {task.value!r} 声明了两次")
        if not isinstance(raw_spec, dict):
            raise ConfigError(
                f"profile.decisions.{task.value} 必须是映射，"
                f"实际是 {type(raw_spec).__name__}"
            )

        instructions = _as_text(raw_spec.get("instructions"))
        if not instructions:
            raise ConfigError(
                f"profile.decisions.{task.value} 没有 instructions —— 一道不告诉"
                "后端它在判什么的题，会让它凭自己的先验作答，而记录上看不出问的是什么"
            )

        labels_raw = raw_spec.get("options")
        if not isinstance(labels_raw, dict) or not labels_raw:
            raise ConfigError(
                f"profile.decisions.{task.value} 的 options 必须是非空映射"
                "（选项 key → 给后端的那句描述）"
            )
        labels: dict[str, str] = {}
        for raw_key, raw_label in labels_raw.items():
            key, label = _as_text(raw_key), _as_text(raw_label)
            if not key or not label:
                raise ConfigError(
                    f"profile.decisions.{task.value} 的选项 {raw_key!r} 缺 key 或描述。\n"
                    "  一个非空的描述是 DecisionQuestion 的硬要求；让它到**编译**时"
                    "才报错的话，那一刻离写坏它的地方已经很远了。"
                )
            labels[key] = label
        tasks[task] = DeclaredTaskSpec(instructions=instructions, labels=labels)

    return DeclaredDecisions(tasks=tasks)


def _as_budget_ceiling(data: dict, *, block: str, key: str) -> int | None:
    """``<block>.<key>`` → 一条额度上限，或 ``None``（没声明）。

    **没声明不是 0。** 读成 0 会让「这份 profile 忘了写额度」表现成「一次都不
    允许」，而那看起来像一次正常的额度用尽 —— 使用方（``application/budget.py``）
    必须能区分它们，所以这里如实返回 ``None``。

    块整体缺失也是 ``None``：额度是**可选声明**。

    两个科目（``search`` / ``fetch``）共用这一份实现，而不是各写一份：两条上限
    的**判据**逐字相同（整数、非负、缺失与 0 分开），而抄第二遍的代价不是重复，
    是漂移 —— 一处补了「bool 不算 int」而另一处没有，于是 ``max_documents: true``
    在某个部署里安静地变成了 1。
    """
    section = data.get(block)
    if not isinstance(section, dict) or key not in section:
        return None
    raw = section[key]
    where = f"profile.{block}.{key}"
    # bool 是 int 的子类 —— ``max_queries: true`` 不该被读成 1。
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(
            f"{where} 必须是整数，实际是 {raw!r}。\n"
            "它是一条额度上限，写成字符串或布尔值之后没有任何地方会把它转换回来。"
        )
    if raw < 0:
        raise ConfigError(f"{where} 不能为负：{raw}")
    return raw


def _as_search_max_queries(data: dict) -> int | None:
    """``search.max_queries`` → 检索额度上限。见 :func:`_as_budget_ceiling`。"""
    return _as_budget_ceiling(data, block="search", key="max_queries")


def _as_fetch_max_documents(data: dict) -> int | None:
    """``fetch.max_documents`` → **一个主体一次复核**的抓取额度上限。见上。"""
    return _as_budget_ceiling(data, block="fetch", key="max_documents")


#: BCP-47 语言标签的形状（**不是**完整校验，装载期只需要拦住写坏的写法）：
#: 主语言子标签（2–3 个字母）+ 可选的子标签（每段 1–8 个字母数字）。
#: 大小写**收**（``EN`` 与 ``en`` 是同一个标签的两种写法），下划线**不收**。
_LOCALE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*")


def _as_search_locales(data: dict) -> tuple[str, ...]:
    """``search.locales`` → 检索时该问**哪种语言**。见 :class:`DeclaredProfile`。

    **形状校验放在装载期，不放在检索期。** 一条写坏的标签（``en_GB``）在检索期
    不会报错，它只是永不生效 —— 而「这一轮按哪种语言问的」恰恰是结果解释不了的那
    部分。装载期报错时，写坏它的地方就在眼前。

    **``locales: []`` 是写坏了的声明，不是「没声明」。** 与 ``search.max_queries``
    那一条的分法同源（``None`` 是没声明、``0`` 是已声明为零）：整块缺失 ⇒ ``()``
    ⇒ 使用方失败关闭；写了键却给一个空表，那是有人打算声明点什么但没写上来，
    在这里红掉比在检索期返回一堆「provider 默认语言」的结果便宜得多。

    **两条塌成一条也是错的。** 这一格只表达**语言**，所以 ``[en-GB, en-US]`` 在
    下游（``application/queries.py::market_locale`` 只取主语言子标签）会合成一条
    ``en``。作者写这两个值，想说的多半是「英国和美国两个市场」—— 而那两个市场是
    ``country`` 那一格的事。静默合成之后，回执上只有一条查询，而作者以为有两条；
    这里红掉，是为了让「哪个国家」这句话必须在**另一格**上说出来。
    """
    section = data.get("search")
    if not isinstance(section, dict) or "locales" not in section:
        return ()
    raw = _as_list(section["locales"], "search.locales")
    tags: list[str] = []
    for item in raw:
        # 不借用 ``_as_text``：它把标量一律转成字符串（``123`` → ``"123"``），
        # 那会让「这一格里放了个映射」与「这一格是条写坏的语言标签」同形 ——
        # 两件不同的错事不该给同一条回执。
        if not isinstance(item, str):
            raise ConfigError(
                f"profile.search.locales 里的 {item!r} 不是字符串"
                f"（实际是 {type(item).__name__}）。\n"
                "这一格是语言标签的列表，每一条都是标量。"
            )
        tag = item.strip()
        if not _LOCALE_TAG.fullmatch(tag):
            raise ConfigError(
                f"profile.search.locales 里的 {item!r} 不是一条 BCP-47 语言标签。\n"
                "形状是「主语言子标签 + 可选的地区/文字子标签」（en、en-GB、zh-Hant）。"
                "下划线不是分隔符 —— ``en_GB`` 这样写不会被任何地方转换回来，"
                "它只会让这一条声明永不生效。"
            )
        tags.append(tag)
    if not tags:
        raise ConfigError(
            "profile.search.locales 是空的。\n"
            "空表不是「没声明」—— 没声明是不写这个键。写了键却一条都不给，"
            "使用方会拿不到语言格而失败关闭，报出来的却是「缺声明」，"
            "而真实情况是这份声明写了一半。"
        )
    # 塌成同一条的两条声明：见 docstring 最后一段。只比主语言子标签 ——
    # ``zh-Hant`` 与 ``zh`` 在这一层是同一个语言（文字子标签不参与选择）。
    # 用与使用方（``application/queries.py``）同一个拆法，免得两处各写一份而漂移。
    primaries = [primary_language_subtag(tag) for tag in tags]
    for primary in dict.fromkeys(primaries):
        same = sorted({tag for tag, one in zip(tags, primaries) if one == primary})
        if len(same) > 1:
            raise ConfigError(
                f"profile.search.locales 里的 {'、'.join(same)} 是同一个语言。\n"
                "语言这一格只到主语言子标签为止，地区部分是 profile.country 那一格的"
                "事 —— 所以这几个值会合成一条查询，而写下来的时候它们看起来是几条。"
                "要按两个国家问，改的是 country，不是这里的地区子标签。"
            )
    # 保序去重：同一份声明里写两遍同一条不是错误，但取值只该有一条。
    return tuple(dict.fromkeys(tags))


def _load_declared_freshness(data: dict) -> FreshnessPolicy | None:
    """``profile.yaml`` 的 ``freshness`` 块 → 判定时限。

    整块缺失返回 ``None``：一份 profile 可以先只有规则、后有时限，而那不是错误
    （与 ``decisions`` 同一条）。**部分缺失不是** —— 写了 ``freshness:`` 却少一个
    阈值，那是一份写坏了的声明，而它必须在这里红。

    为什么不让缺的那一段取一个缺省天数：那组数字（180 / 365 / 730）看起来完全
    合理，却**没有一个**来自获批策略。作为缺省落在装载期，它会变成一份谁都没认过的
    口径，而每一行产出上的 ``currentness`` 都会显得像是按某个业务标准算出来的。
    所以这里只做形状校验，取值一律要求**写出来**。

    形状本身由 :class:`~marketops.domain.temporal.FreshnessPolicy` 自己的校验器管
    （两个阈值的先后、非负数），那条判据**只有一份**，在这里重写一遍就会出现
    两个答案。pydantic 的 ``ValidationError`` 在这里被翻成 ``ConfigError``，因为
    调用方要的是「哪个文件写错了」，不是一条 ``input_type=dict``。
    """
    block = data.get("freshness")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ConfigError(
            f"profile.yaml 的 freshness 必须是映射（policy_version / current_within_days /"
            f" historical_after_days），实际是 {type(block).__name__}"
        )
    try:
        return FreshnessPolicy.model_validate(block)
    except ValidationError as exc:
        raise ConfigError(
            f"profile.yaml 的 freshness 块不成立：{one_line_reason(exc)}\n"
            "  时限是**业务口径**，必须在这一份声明里写全（见 domain/temporal.py）："
            "缺一格时它会以「装载器给了一组缺省天数」的形式被补上，而那些天数"
            "没有任何人认过。"
        ) from exc


def _load_declared_role_matrix(data: dict) -> DeclaredRoleMatrix:
    groups = tuple(
        _role_group(item) for item in _as_list(data.get("role_groups"), "role_groups")
    )
    if not groups:
        raise ConfigError("role-matrix.yaml 的 role_groups 不得为空 —— 空矩阵下不会有任何角色证据")
    excluded = tuple(
        _role_group(item, default_class=RoleClass.EXCLUDED)
        for item in _as_list(data.get("excluded_roles"), "excluded_roles")
    )
    matrix = DeclaredRoleMatrix(
        schema=_as_text(data.get("schema")),
        profile=_as_text(data.get("profile")),
        groups=groups,
        excluded_groups=excluded,
        normalization=dict(data.get("normalization") or {}),
        priority=dict(data.get("priority") or {}),
    )
    _check_group_disjointness(matrix)
    return matrix


def _check_group_disjointness(matrix: DeclaredRoleMatrix) -> None:
    """词表必须无歧义。两条判据，都是装载期失败。

    1. **同一个词不得同时出现在正面组与排除组里。** 两次命中会让同一个岗位既是
       「角色证据」又触发排除 —— 结果取决于求值顺序，而求值顺序是实现的细节，
       不是业务的决定。

    2. **不同分类的词之间不得互为子串。** 匹配是子串匹配，所以
       ``groundskeeper`` 和 ``head groundskeeper`` 会**同时**命中
       "Head Groundskeeper" —— 一次 decision、一次 operational，而引擎只能给
       一个分类。哪个赢取决于词表顺序，那不是一个业务决定。

       因此词表要么用互不包含的写法（``head groundskeeper`` 与 ``grounds manager``），
       要么把两个词放进同一个分类。同分类内的包含关系是允许的
       （``estate manager`` ⊂ ``estates manager`` 无关紧要：两者分类相同）。
    """
    by_term: dict[str, RoleClass] = {}
    for group in matrix.groups + matrix.excluded_groups:
        for term in group.match:
            previous = by_term.get(term)
            if previous is not None and previous is not group.role_class:
                raise ConfigError(
                    f"角色词 {term!r} 同时出现在 {previous.value!r} 与 "
                    f"{group.role_class.value!r} 两类里。\n"
                    "  同一个词只能有一个分类。两份词表都收下它时，实际生效的是"
                    "**后编译的那一份** —— 结果取决于文件里的书写顺序，\n"
                    "  而那不是一个业务决定。请把这个词从其中一组移走。"
                )
            by_term[term] = group.role_class

    terms = sorted(by_term)
    for index, outer in enumerate(terms):
        for inner in terms[index + 1 :]:
            if by_term[outer] is by_term[inner]:
                continue
            if outer in inner or inner in outer:
                raise ConfigError(
                    f"role-matrix 的词 {inner!r} 与 {outer!r} 互为子串，"
                    f"却分属 {by_term[inner].value!r} 与 {by_term[outer].value!r}。\n"
                    "  匹配是子串匹配，一段文本会同时命中两者，而分类只能有一个 ——\n"
                    "  哪个赢将取决于词表顺序，那不是业务决定。"
                )


#: 场景块里允许出现的键，**闭集**。多出来的键不会生效，而块看起来是写过的 ——
#: 拼错 ``page_tiers`` 的那一格会静默地按整份声明写进计划。
_SCENARIO_BLOCK_FIELDS: tuple[str, ...] = (
    "page_tiers",
    "business_functions",
    "class_order",
    "sources",
)


def _load_declared_scenario_matrix(data: dict, *, where: _Where) -> DeclaredScenarioMatrix:
    """读 ``scenario-matrix.yaml``。**只判形状**，与声明的对账在 :func:`_check_scenario_blocks`。

    分两半是因为它们各自需要不同的输入：形状那一半只要这一个文件，而对账那一半
    要 ``profile.yaml`` 的档位与 ``role-matrix.yaml`` 的口径、次序。合成一个函数
    会让「这一块自己写坏了吗」与「这一块与声明对得上吗」在报错上分不开。

    ``where`` 只用于报错时点名（见 :data:`_Where`）：读字节的那一步在调用方，
    所以这条形状校验对「从盘上装载」与「从一个被钉住的 Scene 版本装载」是同一个。
    """
    _require_schema(data, SOP_SCENARIO_MATRIX_SCHEMA, where)
    raw = data.get("blocks")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{where} 缺少 blocks 映射（或者它不是映射）。\n"
            "  一份没有块的场景声明与**没有这份文件**在计划上同形，却比后者多一次"
            "「这件事已经处理过了」的错觉 —— 要么写出块，要么不写这个文件。"
        )
    blocks: dict[str, DeclaredScenarioBlock] = {}
    for key, item in raw.items():
        scenario = _as_text(key)
        if not scenario:
            raise ConfigError(f"{where} 的 blocks 里有一个空键 —— 空键不是任何场景读法")
        blocks[scenario] = _scenario_block(item, where=where, scenario=scenario)
    return DeclaredScenarioMatrix(
        schema=_as_text(data.get("schema")),
        profile=_as_text(data.get("profile")),
        blocks=blocks,
    )


def _scenario_block(item: object, *, where: _Where, scenario: str) -> DeclaredScenarioBlock:
    at = f"{where} 的 blocks.{scenario}"
    if not isinstance(item, dict):
        raise ConfigError(f"{at} 必须是映射，实际是 {type(item).__name__}")
    unknown = tuple(name for name in item if name not in _SCENARIO_BLOCK_FIELDS)
    if unknown:
        raise ConfigError(
            f"{at} 里有不认识的键 {list(unknown)}；这一块只认 "
            f"{list(_SCENARIO_BLOCK_FIELDS)}。\n"
            "  多出来的那一个键不会生效，而块看起来是写过的 —— 静默的收窄与没写的"
            "收窄在计划上同形。"
        )
    missing = tuple(name for name in _SCENARIO_BLOCK_FIELDS if name not in item)
    if missing:
        raise ConfigError(
            f"{at} 少了 {list(missing)}。\n"
            "  这四格**逐格必填**：一格空着不读作「这一格不收窄」，那会让「按这个场景"
            "收窄」与「这个场景什么都没说」在计划上同形。不收窄这一格，就把声明里"
            "那几条原样写出来 —— 抄出来的那几行一眼看得出是抄的，留空看不出。"
        )
    values = {
        name: tuple(_as_text(entry) for entry in _as_list(item[name], f"blocks.{scenario}.{name}"))
        for name in _SCENARIO_BLOCK_FIELDS
    }
    for name, entries in values.items():
        if not entries:
            raise ConfigError(
                f"{at}.{name} 是空的 —— 空着不是「这一格不收窄」，理由与缺键相同"
            )
        if any(not entry for entry in entries):
            raise ConfigError(f"{at}.{name} 里有一条空串")
        duplicates = tuple(sorted({entry for entry in entries if entries.count(entry) > 1}))
        if duplicates:
            raise ConfigError(
                f"{at}.{name} 里 {list(duplicates)} 出现了不止一次。\n"
                "  这一格按声明的次序比对（子序列），重复项会让它无论如何都对不上。"
            )
    return DeclaredScenarioBlock(
        page_tiers=values["page_tiers"],
        business_functions=values["business_functions"],
        class_order=values["class_order"],
        sources=values["sources"],
    )


def _check_scenario_blocks(
    matrix: DeclaredScenarioMatrix,
    *,
    declared: DeclaredProfile,
    roles: DeclaredRoleMatrix,
) -> None:
    """每一个块里的每一格，必须是声明里已经有的名字，且保持声明的次序。

    次序那一条是本函数存在的第二个理由（第一个是拼错的名字）：块能收窄「哪几种」，
    不能改「谁先谁后」—— 先后是声明的口径（``page_discovery_hints`` 的顺序同时是
    抓取前沿，``priority.class_order`` 就是「哪一类更靠前」那句话）。次序对不上时
    报错而不是照块里的次序写，因为照写会让「这份声明说的先后」有两个答案，
    而两个答案不一致时没有一处会红。
    """
    for scenario, block in matrix.blocks.items():
        where = f"blocks.{scenario}"
        _require_narrowing("page_tiers", tuple(declared.page_discovery_hints), block.page_tiers, where=where)
        _require_narrowing("business_functions", roles.business_functions, block.business_functions, where=where)
        _require_narrowing(
            "class_order",
            tuple(str(item) for item in roles.priority.get("class_order", ())),
            block.class_order,
            where=where,
        )
        _require_narrowing("sources", tuple(SOURCE_METHODS), block.sources, where=where)


def _require_narrowing(
    field: str, declared: tuple[str, ...], values: tuple[str, ...], *, where: str
) -> None:
    """``values`` 是不是 ``declared`` 的**子序列**（同序、可以是子集）。"""
    declared_set = set(declared)
    unknown = tuple(name for name in values if name not in declared_set)
    if unknown:
        raise ConfigError(
            f"scenario-matrix.yaml 的 {where}.{field} 里有声明里没有的名字 {list(unknown)}；"
            f"声明里的是 {list(declared)}。\n"
            "  收窄只能收到已声明的那几个：把没声明过的名字写进来，看起来像「这个场景"
            "要这几条」，而实际发生的是那一条**永不生效** —— 计划上一切正常。"
        )
    reordered = tuple(name for name in declared if name in set(values))
    if reordered != values:
        raise ConfigError(
            f"scenario-matrix.yaml 的 {where}.{field} 改了声明的次序：块里写的是 "
            f"{list(values)}，而声明里的次序是 {list(reordered)}。\n"
            "  块能收窄「哪几种」，不能改「谁先谁后」—— 先后是声明的口径"
            "（页面档位的顺序同时是抓取前沿，类别的次序就是「哪一类更靠前」那句话）。"
            "要改先后就改声明，那是一次会被看见的改动。"
        )


def _role_group(item: object, *, default_class: RoleClass | None = None) -> DeclaredRoleGroup:
    if not isinstance(item, dict):
        raise ConfigError(f"role-matrix 的角色组必须是映射，实际是 {type(item).__name__}")
    group_id = _as_text(item.get("id"))
    terms = tuple(_as_text(term) for term in _as_list(item.get("match"), "role_group.match"))
    terms = tuple(term for term in terms if term)
    if not terms:
        raise ConfigError(f"角色组 {group_id!r} 的 match 为空 —— 空组会被静默当成永不匹配")

    weight_raw = item.get("weight", 1.0)
    try:
        weight = float(weight_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"角色组 {group_id!r} 的 weight 非法：{weight_raw!r}") from exc

    raw_class = item.get("class")
    if raw_class is None and default_class is not None:
        role_class = default_class
    else:
        role_class = _as_enum(RoleClass, raw_class, f"角色组 {group_id!r} 的 class")
    if role_class is RoleClass.UNKNOWN:
        raise ConfigError(
            f"角色组 {group_id!r} 的 class 不得是 unknown —— 声明一个组就是为了给它的词一个分类"
        )
    if default_class is RoleClass.EXCLUDED and role_class is not RoleClass.EXCLUDED:
        raise ConfigError(
            f"excluded_roles 里的组 {group_id!r} 的 class 只能是 excluded，实际是 {role_class.value!r}"
        )

    return DeclaredRoleGroup(
        id=group_id,
        role_class=role_class,
        weight=weight,
        match=terms,
        business_function=_as_enum(
            BusinessFunction,
            item.get("business_function", BusinessFunction.UNKNOWN.value),
            f"角色组 {group_id!r} 的 business_function",
        ),
        rationale=_as_text(item.get("rationale")),
    )


def _load_declared_grading(data: dict) -> DeclaredGrading:
    subject = _as_text(data.get("subject"))
    if subject != CONTACT_ROUTE_SUBJECT:
        raise ConfigError(
            f"grading.yaml 的 subject 是 {subject!r}，只支持 {CONTACT_ROUTE_SUBJECT!r}。\n"
            "  定级主语是**一条联系路线**（Account + Person/Department + Role + literal email + Evidence）。\n"
            "  把等级挂在账号上会让同一个账号的多条路线共享一个等级 —— 那正是这次要分开的东西。"
        )

    if "fallback_grade" in data:
        raise ConfigError(
            "grading.yaml 不得声明 fallback_grade。\n"
            "  SOP v1 L125–L129 的 A/B/C/D 四条都是**正面定义**，没有兜底级；"
            "旧 profile 的 fallback_grade: D 配着 D 的 conditions: ['true']，"
            "实际是一条「没被别人拿走就算 D」的规则，与 D 的定义无关。\n"
            "  没匹配上任何一级时引擎返回 X + RULES_UNKNOWN（fail closed），"
            "「信息不足」按 SOP L119 走 blocked/no_route，不走等级。"
        )

    x_rules = tuple(_account_rule(item) for item in _as_list(data.get("x_rules"), "x_rules"))
    if not x_rules:
        raise ConfigError("grading.yaml 的 x_rules 不得为空 —— 没有排除规则时每个账号都会进候选集")

    raw_grades = data.get("grades")
    if not isinstance(raw_grades, dict):
        raise ConfigError("grading.yaml 缺少 grades 映射")

    grades: dict[Grade, DeclaredGrade] = {}
    for grade in Grade:
        raw = raw_grades.get(grade.value)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ConfigError(f"grading.yaml 的 grades.{grade.value} 必须是映射")
        requires = _requires(
            raw, f"grades.{grade.value}", ROUTE_FACT_NAMES, empty_is_allowed=True
        )
        grades[grade] = DeclaredGrade(
            grade=grade,
            label=_as_text(raw.get("label")),
            description=_as_text(raw.get("description")),
            requires=requires or (),
            sop_reference=_as_text(raw.get("sop_reference")),
        )

    page_priorities = _load_page_priorities(data.get("page_priorities"))
    business_entry_locals = _load_business_entry_locals(data.get("business_entry_locals"))
    generic_mailbox_locals = _load_generic_mailbox_locals(data.get("generic_mailbox_locals"))

    # 只统计**还求不了值**的那些规则引用了什么。已经编译出 requires 的规则
    # 不需要生产者 —— 把它们也算进来，这份清单会报出一批「其实已经有了」的缺口，
    # 而一份带假阳性缺口清单的下场是没人再看它。
    expressions = [rule.declared_when for rule in x_rules if rule.requires is None]

    return DeclaredGrading(
        schema=_as_text(data.get("schema")),
        profile=_as_text(data.get("profile")),
        subject=subject,
        x_rules=x_rules,
        grades=grades,
        page_priorities=page_priorities,
        business_entry_locals=business_entry_locals,
        generic_mailbox_locals=generic_mailbox_locals,
        scoring=dict(data.get("scoring") or {}),
        evidence_rules=dict(data.get("evidence_rules") or {}),
        ungradeable=_referenced_fact_roots(expressions),
    )


def _load_page_priorities(value: object) -> dict[str, str]:
    """P1–P4 的记录块绑定语义（SOP v1 L105）。

    四个都要在，且都要有文字说明 —— 少一个不是「那一级没有规则」，
    是这份 SOP 没被装载完整。
    """
    if not isinstance(value, dict) or not value:
        raise ConfigError(
            "grading.yaml 缺少 page_priorities —— P1–P4 是记录块绑定语义，不是可选项"
        )
    declared = {_as_text(key): _as_text(item) for key, item in value.items()}
    missing = [priority.value for priority in RecordBlockPriority if priority.value not in declared]
    if missing:
        raise ConfigError(f"grading.yaml 的 page_priorities 缺少：{', '.join(missing)}")
    unknown = sorted(set(declared) - {priority.value for priority in RecordBlockPriority})
    if unknown:
        raise ConfigError(f"grading.yaml 的 page_priorities 出现未知优先级：{', '.join(unknown)}")
    empty = sorted(key for key, text in declared.items() if not text)
    if empty:
        raise ConfigError(f"grading.yaml 的 page_priorities 这些条目没有说明文字：{', '.join(empty)}")
    return declared


def _load_business_entry_locals(value: object) -> dict[str, BusinessFunction]:
    """邮箱 local part 词元 → 业务口径。

    这两张表都允许留空吗？**不允许**。空表不是「本 profile 没有业务入口」，
    而是「C 级永远不可能命中」—— 那是一条静默的规则失效，
    而它看起来完全正常。要么给出词表，要么显式承认 C 级在本 profile 里不可达。
    """
    if not isinstance(value, dict) or not value:
        raise ConfigError(
            "grading.yaml 缺少 business_entry_locals —— 空表会让 C 级永不命中，"
            "而它看起来完全正常。"
        )
    table: dict[str, BusinessFunction] = {}
    for raw_token, raw_function in value.items():
        token = _as_text(raw_token).lower()
        if not token:
            raise ConfigError(f"business_entry_locals 的词元非法：{raw_token!r}")
        if any(ch in token for ch in " @._-+"):
            raise ConfigError(
                f"business_entry_locals 的词元 {token!r} 含分隔符或 @。\n"
                "  这张表按**词元**匹配（local part 先按 . _ - + 切分），"
                "所以这里只写单个词元，不写整段 local part。"
            )
        table[token] = _as_enum(
            BusinessFunction, raw_function, f"business_entry_locals.{token}"
        )
    return table


def _load_generic_mailbox_locals(value: object) -> tuple[str, ...]:
    """泛化官方邮箱的 local part（SOP L128 的 D 级）。"""
    if not isinstance(value, list) or not value:
        raise ConfigError(
            "grading.yaml 缺少 generic_mailbox_locals —— 空表会让 D 级永不命中，"
            "而 D 级在 SOP L128 里是明确存在的（低优先触达储备）。"
        )
    locals_: list[str] = []
    for item in value:
        token = _as_text(item).lower()
        if not token:
            raise ConfigError(f"generic_mailbox_locals 的取值非法：{item!r}")
        if any(ch in token for ch in " @._-+"):
            raise ConfigError(
                f"generic_mailbox_locals 的取值 {token!r} 含分隔符或 @。"
                "这里写的是**整个 local part**，不是词元。"
            )
        locals_.append(token)
    return tuple(dict.fromkeys(locals_))


def _requires(
    raw: dict, field_name: str, allowed: tuple[str, ...], *, empty_is_allowed: bool
) -> tuple[str, ...] | None:
    """读取并**校验**一条 ``requires``。

    ``empty_is_allowed=False`` 时，空列表是一个错误而不是「没有条件」：
    排除规则上空列表的含义是两可的，而两可的那一种会清空名单。
    """
    if "requires" not in raw:
        return None
    clauses = tuple(
        _as_text(item) for item in _as_list(raw.get("requires"), f"{field_name}.requires")
    )
    clauses = tuple(clause for clause in clauses if clause)
    if not clauses and not empty_is_allowed:
        raise ConfigError(
            f"{field_name}.requires 是空列表。\n"
            "  排除规则的空条件有两种读法（恒真 → 排除一切 / 不可求值 → 永不命中），"
            "  因此不接受空列表：要么写出可求值的子句，要么整个省略 requires 键"
            "（省略 = 尚未能求值，永不命中并记录在案）。"
        )
    for clause in clauses:
        problem = clause_is_well_formed(clause, allowed)
        if problem:
            raise ConfigError(f"{field_name}.requires 的 {clause!r}：{problem}")
    return clauses


def _account_rule(item: object) -> AccountRule:
    if not isinstance(item, dict):
        raise ConfigError(f"x_rules 的条目必须是映射，实际是 {type(item).__name__}")
    rule_id = _as_text(item.get("id"))
    if not rule_id:
        raise ConfigError("x_rules 的条目需要 id —— 没有 id 的规则无法被引用为 unchecked")

    status = _as_enum(
        AccountStatus, item.get("status", AccountStatus.IRRELEVANT.value), f"x_rule {rule_id!r} 的 status"
    )
    if status not in _EXCLUSION_STATUSES:
        allowed = ", ".join(sorted(item.value for item in _EXCLUSION_STATUSES))
        raise ConfigError(
            f"x_rule {rule_id!r} 的 status 是 {status.value!r}，排除规则只能是 {allowed}"
        )

    # declared_when 是 SOP 原话（表达式方言），保留给审计看；requires 是引擎方言。
    # 两者都缺的规则既不能求值、也没有出处，属于没写完。
    #
    # 校验用的是**并集**（账号自身字段 ∪ 候选事实），不是 ``ACCOUNT_FACT_NAMES``：
    # 只放行「已经有生产者」的名字，作者就没法把一条已知缺生产者的事实写成
    # requires —— 他只能退回散文，而散文不进 unchecked，「等生产者」就看不见了。
    # 运行期的值仍然由 ``account_facts`` 决定，那是**另一个**检查。
    declared_when = _as_text(item.get("when"))
    requires = _requires(
        item, f"x_rule {rule_id!r}", ACCOUNT_QUALIFICATION_FACT_NAMES, empty_is_allowed=False
    )
    if not declared_when and requires is None:
        raise ConfigError(
            f"x_rule {rule_id!r} 既没有 when（SOP 原话）也没有 requires（可求值子句）—— "
            "这条规则没有任何内容"
        )

    return AccountRule(
        id=rule_id,
        description=_as_text(item.get("description")),
        status=status,
        requires=requires,
        declared_when=declared_when,
    )


def _referenced_fact_roots(expressions: Iterable[str]) -> tuple[str, ...]:
    """提取表达式引用到的 ``account.<第一跳>``，保序去重。

    只到第一跳：``account.evidence.natural_grass.verified`` 归到 ``account.evidence``。
    这个粒度是为了让清单读起来像「引擎要能判，得先有什么」，而不是把每个叶子
    路径都列一遍。
    """
    roots: list[str] = []
    for expression in expressions:
        for match in _FACT_REF_RE.findall(expression):
            root = ".".join(match.split(".")[:2])
            if root not in roots:
                roots.append(root)
    return tuple(roots)


def _engine_rules(declared: DeclaredGrading) -> dict[Grade, GradingRule]:
    """把 SOP 声明投影成**引擎契约**。

    投影的门槛是「引擎的子句方言能不能表达它」，不是「业务有没有说」。

    * ``A``/``B``/``C``/``D`` —— SOP 的五条定义说的都是**路线**的事实
      （职能、literal 邮箱、绑定强度、入口性质），引擎的路线事实正好覆盖它们。
      ``requires`` 因此逐条编译出来，不再拒绝判定。
    * ``X`` —— 语义固定（「未判定」），``requires=()`` 是显式恒真，
      永远兜底。SOP 的 ``x_rules`` 是**账号准入**的规则，不是它的内容 ——
      两者在旧 profile 里被混为一谈，见 ``AccountRule``。
    """
    rules: dict[Grade, GradingRule] = {}
    for grade in Grade:
        declared_grade = declared.grades.get(grade)
        if grade is Grade.X:
            rules[grade] = GradingRule(
                grade=grade,
                definition=_grade_definition(declared_grade)
                if declared_grade is not None
                else "未判定：无合格证据 / 证据归属冲突 / 排除类别",
                requires=(),
                sop_reference=declared_grade.sop_reference if declared_grade else "",
                raw={},
            )
            continue

        if declared_grade is None:
            rules[grade] = GradingRule(grade=grade)
            continue

        rules[grade] = GradingRule(
            grade=grade,
            definition=_grade_definition(declared_grade),
            requires=declared_grade.requires,
            sop_reference=declared_grade.sop_reference,
            raw={"requires": list(declared_grade.requires)},
        )
    return rules


def _grade_definition(declared_grade: DeclaredGrade) -> str:
    label = declared_grade.label or declared_grade.grade.value
    if not declared_grade.description:
        return label
    return f"{label}：{declared_grade.description}"


@dataclass(frozen=True)
class _RoleVocabulary:
    """role matrix 编译出的三张表 + 词表。"""

    terms: tuple[str, ...]
    classes: dict[str, RoleClass]
    functions: dict[str, BusinessFunction]
    excluded: tuple[str, ...]


def _compile_role_vocabulary(role_matrix: DeclaredRoleMatrix) -> _RoleVocabulary:
    """把 role matrix 编译成 term → (class, business function)。

    SOP 声明 ``match`` 是「子串匹配、大小写不敏感、命中任意一条即算命中该组」，
    与 ``domain.extract.extract_roles`` 的语义逐字相同 —— 所以这里可以直取。

    同一个词出现在多个正面组时**不是**取并集：那是词表的作者在说两件不同的事，
    而引擎只能给一个分类。装载期直接失败，让作者自己决定它属于哪一组。
    """
    classes: dict[str, RoleClass] = {}
    functions: dict[str, BusinessFunction] = {}
    terms: list[str] = []

    for group in role_matrix.groups:
        for term in group.match:
            previous = classes.get(term)
            if previous is not None and previous is not group.role_class:
                raise ConfigError(
                    f"角色词 {term!r} 同时属于 {previous.value!r} 与 {group.role_class.value!r} 两类。\n"
                    "  一个词只能有一个分类；请把它从其中一组移走。"
                )
            classes[term] = group.role_class
            functions[term] = group.business_function
            terms.append(term)

    excluded: list[str] = []
    for group in role_matrix.excluded_groups:
        for term in group.match:
            classes[term] = RoleClass.EXCLUDED
            functions[term] = group.business_function
            excluded.append(term)

    return _RoleVocabulary(
        terms=tuple(dict.fromkeys(terms)),
        classes=classes,
        functions=functions,
        excluded=tuple(dict.fromkeys(excluded)),
    )


def _unknowns(declared: DeclaredGrading) -> tuple[str, ...]:
    """引擎还判不了的**原因清单**。engine 的拒绝理由直接引用它。

    措辞必须说清是哪一种「判不了」。业务没给规则和业务给了但引擎求不了值，
    对 engine 是同一种反应（拒绝），对读审计的人是两件完全不同的事 ——
    前者要去找业务，后者要去写事实生产者。

    路线侧现在没有缺口（A/B/C/D 都已编译成可求值子句），账号侧也不是「业务没说」
    （六条 ``x_rules`` 全部编译出了可求值的 ``requires``），所以这份清单今天是**空**的。

    **本函数说装载期，``AccountOutcome.unchecked`` 说运行期。** 装载期问「这个词
    引擎认识吗」，运行期问「这个键今天有值吗」。两者会漂移，而漂移的后果是同一个
    判词（``DISCOVERED`` + ``UNRESOLVED_FACTS``）—— 所以这份清单为空**不能**读成
    「账号侧已经对齐」，它只说明没有规则再因为**事实名缺失**求不了值。引用候选事实
    的那五条今天仍然一条都判不了，判不了发生在运行期，而它的承载点是
    ``AccountOutcome.unchecked``（那份清单非空）。
    """
    reasons: list[str] = []
    for rule in declared.x_rules:
        if rule.requires is None:
            reasons.append(
                f"account.eligibility.{rule.id}: SOP 已声明排除条件，引擎尚无事实生产者"
            )
    if declared.ungradeable:
        reasons.append("待生产的事实：" + "、".join(declared.ungradeable))
    return tuple(reasons)


__all__ = [
    # 装载器本体。两个入口，**同一条校验序列**：从盘上的目录读，以及从一批
    # 已经冻住的 Scene 字节读（判定与结算那两条路要的是后者 —— 见
    # ``load_profile_from_scene_files``）。
    "load_profile",
    "load_profile_from_scene_files",
    "read_scene_files",
    # 转发自 ``marketops.domain.profile`` —— 本模块仍是这些名字的公开入口
    "AccountRule",
    "DeclaredGrade",
    "DeclaredGrading",
    "DeclaredProfile",
    "DeclaredRoleGroup",
    "DeclaredRoleMatrix",
    "FreshnessPolicy",
    "GradingRule",
    "ProfileBundle",
    # 装载契约常量
    "CONTACT_ROUTE_SUBJECT",
    "SOP_PROFILE_SCHEMA",
    "SOP_GRADING_SCHEMA",
    "SOP_ROLE_MATRIX_SCHEMA",
]
