"""``mint-token``：铸一个令牌，只显示一次，且落在仓库之外（§0 / §7）。

## 为什么这条子命令必须有测试，而不是「运维会用对」

``marketops/security/tokens.py`` 的 ``from_file`` 在文件缺失时的报错**已经**指名
了 ``python -m marketops.interfaces.cli mint-token``。一条被别的模块点名的命令，
如果不存在或者行为与那句话不符，读者会先怀疑自己的环境 —— 这个仓库的纪律是
「提到一个名字，那个名字就得真的是它」。

## 四件事被钉住

1. **令牌只出现在 stdout，且只有一行。** ``TOKEN=$(marketops mint-token ...)``
   是它被使用的样子，所以那一行前面不能有任何东西（``$( )`` 只吃尾部换行）。
2. **盘上只有摘要。** 这是「只显示一次」的代价，也是它成立的理由 —— 文件被
   误贴进工单时不会泄露一个可用的凭据。
3. **落点必须在仓库之外。** 这条是 §0 的硬约束，而 ``--out`` 是它唯一的入口。
4. **受众与有效期必填，且原样写进环。** 这两格是「这个令牌发给谁、到什么时候
   为止」的全部记录，而它们的缺省值只可能是本命令**猜**出来的一个部署事实：
   缺省受众会猜成「本机」（反向代理部署里恰好错的那一个），缺省有效期会猜成
   一个没人评审过的数字（而那是一次安全策略）。猜错的那一刻就固化进环文件了，
   所以两个都是必填参数 —— 这条判据连同「报错要说得出该传什么」一起钉住。
"""

from __future__ import annotations

import json
import re

import pytest

from marketops.config import REPO_ROOT
from marketops.domain.errors import ConfigError
from marketops.interfaces.cli import main
from marketops.security.tokens import TokenRing, check_credential_path, digest_of
from tests.conftest import DEFAULT_PROFILE
from tests.mcp_harness import MCP_URL

#: 铸令牌时用的受众、工作区与有效期。受众取脚手架里那个**服务端也会认定**的值；
#: 工作区取那个 profile（``conftest.DEFAULT_PROFILE``），也就是同一条路径上服务端
#: 会认作自己 Scene 的值。两处写成一个来源，一次「测试铸的与服务端等的不一样」
#: 就不会伪装成「认证配错了」。
AUDIENCE = MCP_URL
WORKSPACE = DEFAULT_PROFILE
EXPIRES_IN = "30d"


def _mint(
    tmp_path,
    *,
    principal: str = "claude-control",
    role: str = "controller",
    rotate: bool = False,
    audience: str | None = AUDIENCE,
    workspace: str | None = WORKSPACE,
    expires_in: str | None = EXPIRES_IN,
):
    """跑一次 ``mint-token``，返回 ``(退出码, 环文件路径)``。

    ``audience`` / ``workspace`` / ``expires_in`` 可以显式传 ``None`` 来**省掉**
    对应的参数 —— 那正是「必填」那几条判据要复现的命令行。
    """
    target = tmp_path / "tokens.json"
    argv = ["mint-token", "--principal", principal, "--role", role, "--out", str(target)]
    if audience is not None:
        argv += ["--audience", audience]
    if workspace is not None:
        argv += ["--workspace", workspace]
    if expires_in is not None:
        argv += ["--expires-in", expires_in]
    if rotate:
        argv.append("--rotate")
    return main(argv), target


# ----------------------------------------------------------------------
# 输出契约
# ----------------------------------------------------------------------
def test_the_token_is_the_only_thing_on_stdout(tmp_path, capsys) -> None:
    """``$(...)`` 拿到的必须是干净的一行。

    一个前导空行或一句进度说明，会让调用方把一个带解释的字符串当令牌用 ——
    而那次失败看起来像认证配错了，排查方向会跑去环上。
    """
    code, _ = _mint(tmp_path, principal="worker-1", role="researcher")
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.count("\n") == 1, f"stdout 不止一行令牌：{captured.out!r}"
    assert captured.out == captured.out.strip() + "\n", "令牌前后有多余的空白"
    assert captured.err, "说明文字应当走 stderr —— 否则它迟早混进 stdout"


def test_the_explanation_does_not_contain_the_token(tmp_path, capsys) -> None:
    """说明里既没有令牌也没有摘要。

    它会被人复制进工单。一句「已写入 X 个主体」是有用的；一句带着摘要的
    「已写入 <sha256>」就是把一份可以离线爆破的东西抄送了出去。
    """
    _, target = _mint(tmp_path)
    captured = capsys.readouterr()
    token = captured.out.strip()
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert token not in captured.err
    for entry in payload["tokens"]:
        assert entry["digest"] not in captured.err


def test_what_lands_on_disk_is_the_digest_not_the_token(tmp_path, capsys) -> None:
    _, target = _mint(tmp_path)
    token = capsys.readouterr().out.strip()
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert [entry["digest"] for entry in payload["tokens"]] == [digest_of(token)]
    assert token not in target.read_text(encoding="utf-8")


def test_the_written_ring_is_readable_and_grants_what_was_asked(tmp_path, capsys) -> None:
    """铸出来的东西必须真的能被服务面装载 —— 否则它只是一个文件。

    受众那一格也在这里读回一次：**写进环的受众就是命令行上给的那个**。`auth.py`
    的每一次请求都拿它与本服务认定的资源比，所以「命令行收下了却没写进去」的
    症状是每一条令牌都 401 —— 而那时命令行刚刚报过成功。
    """
    _, target = _mint(tmp_path, principal="worker-1", role="researcher")
    token = capsys.readouterr().out.strip()

    grant = TokenRing.from_file(target).grant_for(token)

    assert grant is not None, "刚铸的令牌装不进环 —— 它不是一个可用的凭据"
    assert grant.principal == "worker-1"
    assert [role.value for role in grant.roles] == ["researcher"]
    assert grant.audience == AUDIENCE, "命令行给的受众没有写进环"
    assert grant.workspaces == {WORKSPACE}, "命令行给的工作区没有写进环"
    assert grant.expires_at > 0


# ----------------------------------------------------------------------
# 受众、工作区与有效期：必填，且原样写进环
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("audience", "workspace", "expires_in", "missing"),
    [
        (None, WORKSPACE, EXPIRES_IN, "--audience"),
        (AUDIENCE, None, EXPIRES_IN, "--workspace"),
        (AUDIENCE, WORKSPACE, None, "--expires-in"),
        (None, None, None, "--audience"),
    ],
    ids=["audience-only", "workspace-only", "expires-in-only", "all"],
)
def test_all_three_new_fields_are_required(
    tmp_path, capsys, audience, workspace, expires_in, missing
) -> None:
    """少一个就不是「用缺省值」，是**这次调用不成立**。

    argparse 的 ``SystemExit(2)`` 也在这里被当成期望结果之一：它是命令行层对
    「必填参数缺失」的既有处置，与 ``ConfigError`` 那条路径（退 2 加一句 stderr）
    汇成同一个退出码 —— 对运维来说两种都是「命令没跑成」，只是理由在 stderr 里。
    """
    target = tmp_path / "tokens.json"
    argv = ["mint-token", "--principal", "worker-1", "--role", "researcher", "--out", str(target)]
    if audience is not None:
        argv += ["--audience", audience]
    if workspace is not None:
        argv += ["--workspace", workspace]
    if expires_in is not None:
        argv += ["--expires-in", expires_in]

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capsys.readouterr()

    assert exit_info.value.code == 2
    assert missing in captured.err, "报错要点名缺的是哪一个参数"
    assert not target.exists(), "参数不全却把环写出来了"


def test_a_workspace_that_is_not_a_slug_is_refused_before_anything_is_written(
    tmp_path, capsys
) -> None:
    """工作区名不是一个「随便什么字符串」。

    它是 ``Settings.profile_dir`` 的最后一个路径段（``profiles_dir / profile``），
    所以两个只在空白或大小写上不同的写法会是两个字符串、只可能有一个目录 ——
    而它们同属一个 Scene。校验在**写之前**：写进环之后再发现，那份环就是一份
    装载失败的环，而它看起来与一次成功的铸造没有区别。
    """
    _, target = _mint(tmp_path, workspace="UK Acmecorp")
    captured = capsys.readouterr()

    assert captured.out == "", "被拒绝的调用不该回显任何东西"
    assert not target.exists()
    assert "UK Acmecorp" in captured.err


def test_a_workspace_may_be_repeated_and_a_repeat_is_not_a_second_grant(tmp_path, capsys) -> None:
    """``--workspace`` 可重复（一条令牌准动多个 Scene），而重复同一个值只是笔误。

    环里那一格写成**排序去重后的列表**：一份「同样的授权两次铸出来字节不同」的环
    会让 diff 里全是噪音，而两份环在判定上完全相同。
    """
    target = tmp_path / "tokens.json"
    code = main(
        [
            "mint-token",
            "--principal",
            "worker-1",
            "--role",
            "researcher",
            "--audience",
            AUDIENCE,
            "--workspace",
            "uk-second-scene",
            "--workspace",
            WORKSPACE,
            "--workspace",
            WORKSPACE,
            "--expires-in",
            EXPIRES_IN,
            "--out",
            str(target),
        ]
    )
    token = capsys.readouterr().out.strip()

    assert code == 0
    grant = TokenRing.from_file(target).grant_for(token)

    assert grant is not None
    assert grant.workspaces == {WORKSPACE, "uk-second-scene"}
    assert json.loads(target.read_text(encoding="utf-8"))["tokens"][0]["workspaces"] == [
        WORKSPACE,
        "uk-second-scene",
    ], "盘上那一格没有排序去重 —— 两次同样的铸造会写出不同的字节"


def test_the_summary_says_which_audience_and_until_when(tmp_path, capsys) -> None:
    """stderr 要报出刚写进去的受众与失效时刻。

    理由与那条 401 一样硬：受众错配与令牌写错在传输层上是同一个 401，**响应体
    里没有放理由的位置**。于是诊断只能在已认证的这一侧做 —— 铸的时候就报出
    「这个令牌是给谁的、到什么时候」，是这条路径上唯一一处能提前发现配置错的地方。

    有效期断言的是一个**时刻**而不是「一个数」：``30d`` 写进去以后，人读到的应当
    是「到哪一天为止」，而不是让他自己拿今天的日期去加三十天。断言因此是
    「那句里有一个 ISO 时刻，它等于现在 + 30 天（±几秒）」—— 而不是「句子里
    出现了 30 这个子串」，后者在 ``2592000 秒`` 与任何含 30 的日期上都成立。
    """
    from datetime import datetime, timezone

    before = int(datetime.now(tz=timezone.utc).timestamp())
    _mint(tmp_path, principal="worker-1", role="researcher")
    captured = capsys.readouterr()
    after = int(datetime.now(tz=timezone.utc).timestamp())

    assert AUDIENCE in captured.err
    # 工作区那一格同一条理由：它错配时**不是** 401，而是每一条业务工具都被拒，
    # 而拒绝里不报出本部署的 Scene —— 所以铸的时候就该把刚写下的那个值报出来。
    assert WORKSPACE in captured.err, "说明里没有报出刚写进去的工作区"

    found = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", captured.err)
    assert found, f"说明里没有可读的失效时刻：{captured.err!r}"
    moment = int(datetime.fromisoformat(found.group().replace("Z", "+00:00")).timestamp())
    assert before + 2592000 <= moment <= after + 2592000, (
        f"报出的失效时刻 {found.group()} 不是「现在 + 30 天」"
    )
    # 说明里要指出诊断入口，否则读到「一律 401」的人不知道下一步该看什么。
    assert "--check" in captured.err


def test_a_duration_the_parser_refuses_is_a_refusal_not_a_default(tmp_path, capsys) -> None:
    """``never`` 这类写法在铸的时候就被拒，绝不落成「永不过期」。

    这一条是「每条授权都有有效期」那条要求在本文件里的执行器：一个把解析失败
    吞成缺省值的实现会让 ``--expires-in never`` 成功，而它在环里长得与一个正常
    条目完全一样。
    """
    _, target = _mint(tmp_path, expires_in="never")
    captured = capsys.readouterr()

    assert captured.out == "", "被拒绝的调用不该回显任何东西"
    assert not target.exists()
    assert "never" in captured.err or "有效期" in captured.err


def test_an_audience_that_is_not_a_url_is_refused(tmp_path, capsys) -> None:
    """受众必须是绝对 http(s) URL —— 校验在**写之前**。

    一个收下 ``/mcp`` 的实现在 ``TokenRing`` 装载期会炸，而那时离这次命令行已经
    很远：环文件在盘上，服务面起不来，报错指向的是环而不是这条命令。
    """
    _, target = _mint(tmp_path, audience="/mcp")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert not target.exists()
    assert "/mcp" in captured.err


def test_the_file_is_not_world_readable(tmp_path) -> None:
    """0600。一份 0644 的凭据文件与一份公开的凭据文件没有区别。"""
    _, target = _mint(tmp_path)
    assert target.stat().st_mode & 0o077 == 0, oct(target.stat().st_mode)


# ----------------------------------------------------------------------
# 落点：仓库之外
# ----------------------------------------------------------------------
def test_a_target_inside_the_repository_is_refused(tmp_path) -> None:
    """§0：凭据永不进仓。拒绝必须发生在**写之前**。

    ``--out`` 指向仓库里是最可能发生的一次误用（人就在仓库目录下敲命令），
    而它一旦成功，那次提交与别的提交长得一模一样。

    这条打的是**判据本身**（``check_credential_path``），而不是 CLI 的退出码 ——
    CLI 那一侧把 ``ConfigError`` 翻译成退出码 2，两条断言分别钉住两层：这里钉
    「拒绝发生」，下面 ``test_the_cli_reports_a_refusal_as_exit_code_two`` 钉
    「拒绝被人看见」。
    """
    inside = REPO_ROOT / "tmp-mint-token-do-not-create.json"

    with pytest.raises(ConfigError) as excinfo:
        check_credential_path(inside, repo_root=REPO_ROOT)

    assert "仓库" in str(excinfo.value)
    assert not inside.exists(), "拒绝发生在写之后 —— 差一点就提交进去了"


def test_a_missing_directory_is_an_error_not_a_mkdir(tmp_path) -> None:
    """不创建目录。

    为一个凭据文件顺手把目录树造出来，正是「凭据出现在谁都没想到的地方」
    最常见的成因。路径写错了应当是报错。
    """
    target = tmp_path / "nope" / "tokens.json"
    with pytest.raises(ConfigError):
        check_credential_path(target, repo_root=REPO_ROOT)
    assert not target.parent.exists(), "报错的路径被顺手建了出来"


def test_the_cli_reports_a_refusal_as_exit_code_two(tmp_path, capsys) -> None:
    """``ConfigError`` 在 CLI 这一层是**退出码 2 加一句解释**，不是堆栈。

    这是本仓 CLI 的既有契约（``main`` 里的 ``except ConfigError``），值得在
    ``mint-token`` 上再钉一次：这条子命令是唯一会写凭据的，让它的失败形态
    与别的子命令不同（比如抛出去）会让运维在一个需要确定性最的地方拿到
    不确定的东西。
    """
    inside = REPO_ROOT / "tmp-mint-token-do-not-create.json"

    code = main(
        [
            "mint-token",
            "--principal",
            "claude-control",
            "--role",
            "controller",
            "--audience",
            AUDIENCE,
            "--workspace",
            WORKSPACE,
            "--expires-in",
            EXPIRES_IN,
            "--out",
            str(inside),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "仓库" in captured.err
    assert captured.out == "", "失败时 stdout 必须是空的 —— 有人会把它当令牌收下"
    assert not inside.exists()


# ----------------------------------------------------------------------
# 环的合并语义
# ----------------------------------------------------------------------
def test_a_second_principal_joins_the_ring(tmp_path, capsys) -> None:
    """铸第二个主体不能把第一个挤掉 —— 否则两套令牌会互相使对方失效。"""
    _, target = _mint(tmp_path, principal="claude-control", role="controller")
    first = capsys.readouterr().out.strip()
    _mint(tmp_path, principal="worker-1", role="researcher")
    second = capsys.readouterr().out.strip()

    ring = TokenRing.from_file(target)

    assert ring.grant_for(first) is not None
    assert ring.grant_for(second) is not None
    assert ring.principal_names == ("claude-control", "worker-1")


def test_re_minting_the_same_principal_is_refused_without_rotate(tmp_path, capsys) -> None:
    """同一主体默认**不许**再铸一次。

    一个会默默替换既有授权的实现，会让「谁还能调」在没人察觉的情况下改变 ——
    而那条旧令牌的持有者只会看到 401，排查方向是「令牌过期」。
    """
    _mint(tmp_path, principal="worker-1", role="researcher")
    before = capsys.readouterr().out.strip()

    code, _ = _mint(tmp_path, principal="worker-1", role="researcher")
    captured = capsys.readouterr()

    assert code == 2
    assert "--rotate" in captured.err, "拒绝理由要说出该怎么继续"
    assert captured.out == "", "被拒绝的调用不该回显任何东西"
    assert TokenRing.from_file(tmp_path / "tokens.json").grant_for(before) is not None, (
        "被拒绝的那次写入还是改动了盘上的环"
    )


def test_rotate_replaces_the_token_and_says_so(tmp_path, capsys) -> None:
    """``--rotate`` 是那条**明说**的路径，且旧令牌真的失效。"""
    _, target = _mint(tmp_path, principal="worker-1", role="researcher")
    old = capsys.readouterr().out.strip()
    _mint(tmp_path, principal="worker-1", role="researcher", rotate=True)
    new = capsys.readouterr().out.strip()

    ring = TokenRing.from_file(target)

    assert new != old
    assert ring.grant_for(new) is not None
    assert ring.grant_for(old) is None, "轮换之后旧令牌还能用 —— 那不是轮换"


def test_rotating_with_a_different_role_replaces_it_and_keeps_the_ring_loadable(
    tmp_path, capsys
) -> None:
    """换角色只能靠轮换，而轮换必须**替换**而不是追加。

    ``TokenRing`` 在装载期就禁止一个主体带两套角色（会话归属按主体比对、
    **不看** scope，所以两套角色会让两侧令牌在会话层不可区分）。因此「给同一个
    人换个角色」只有一条正确的落法：替换掉那条旧授权。一个追加的实现会写出
    一个**装载失败**的环 —— 而那会让服务面在下一次启动时起不来，症状看起来
    与令牌轮换毫无关系。
    """
    _, target = _mint(tmp_path, principal="worker-1", role="researcher")
    old = capsys.readouterr().out.strip()
    _mint(tmp_path, principal="worker-1", role="controller", rotate=True)
    captured = capsys.readouterr()
    new = captured.out.strip()

    ring = TokenRing.from_file(target)  # 装载失败 = 这条测试的失败方式

    assert ring.principal_names == ("worker-1",), "轮换把一个主体写成了两个条目"
    assert ring.roles_present == ("controller",), "旧角色没有被换掉"
    assert ring.grant_for(old) is None
    assert ring.grant_for(new) is not None
    assert "替换" in captured.err, "换了别人的权限却没说 —— 说明里要写清楚是替换"


def test_an_unknown_role_is_refused(tmp_path, capsys) -> None:
    """拼错的角色名在铸的时候就发现，不留到装载期。

    装载期那条判据（``TokenRing._parse_entry``）拦的是别人手工编辑出来的环；
    这条拦的是命令行上的一次手滑，而它离操作者更近 —— 说得出「已知角色是哪些」。
    """
    code, target = _mint(tmp_path, principal="worker-1", role="admin")
    captured = capsys.readouterr()

    assert code == 2
    assert "admin" in captured.err
    assert "controller" in captured.err, "拒绝理由要列出已知角色"
    assert not target.exists(), "角色名不认识却把环写出来了"


def test_a_broken_ring_is_not_silently_repaired(tmp_path, capsys) -> None:
    """已经坏掉的环不得被本次写入「修好」。

    那会把一次故障变成一次静默的凭据替换：盘上原来那份授权不见了，而命令
    报了成功。装载器早就为这件事准备了判据，这里只是不绕过它。
    """
    target = tmp_path / "tokens.json"
    target.write_text("{not json", encoding="utf-8")

    code, _ = _mint(tmp_path, principal="claude-control", role="controller")
    captured = capsys.readouterr()

    assert code == 2
    assert captured.err, "坏环被默默忽略了 —— 它必须是一句人能读到的拒绝"
    assert target.read_text(encoding="utf-8") == "{not json", "坏文件被改写了"
