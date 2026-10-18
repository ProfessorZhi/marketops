"""母池回执的不变量（``marketops/domain/receipt.py``）。

这些判据挡的不是「写错了」，是一类**在报告上看起来正常**的失败。母池这一层
最贵的错误不是崩溃，是安静：少了几行、空池被读成做完、只有标识的行被当主体发出去。
三种在那份报告里与「一切正常」长得一样，而三种的代价都是真金白银（一次检索、
一次模型调用、或者一个从错误的分母算出来的覆盖率）。

所以本文件的断言分成三组，对应上面三件事：

1. **每一行都要有处置，而且不多不少** —— 序号必须是 ``0..raw_row_count-1`` 的排列。
2. **空池不是成功** —— ``raw_row_count >= 1``，且没有任何 ``resolved`` 行时
   ``blocker`` 说得出理由。
3. **只有标识的行不能开工** —— ``pending`` 的行在类型层面带不了 ``resolved_name``，
   而 ``resolved`` 的行必须带。

另有一组是重放：同样的输入 → 同一个 ``receipt_id``；改过的规则 → 另一个。
它挡的是「第二次导入覆盖了第一次，于是两次结论不同这件事在盘上不留痕迹」。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marketops.domain.enums import DomainVerdict
from marketops.domain.errors import ConfigError
from marketops.domain.receipt import (
    RECEIPT_VERSION,
    Disposition,
    RawCandidateRow,
    RowDisposition,
    SourceReceipt,
    dispositions_digest,
    receipt_id_for,
    row_id_for,
    verify_receipt_id,
)

# --- 测试替身：一个说得通的回执 ------------------------------------------------

_CREATED_AT = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
_SHA = "a" * 64


def _row(row_id: str = "row-1", ordinal: int = 0, **overrides: object) -> RowDisposition:
    """一个 ``resolved`` 的处置 —— 五个成员里唯一不需要 ``reason`` 的那一个。

    其余四种都靠 ``**overrides`` 构造，于是每一条判据的测试里**只有它要考的那
    一格与默认值不同**。这是刻意的：一个把五格一起改掉的测试，在失败时说不出
    是哪一格被拒的。

    ``resolved_name`` 只在处置真是 ``resolved`` 时补默认。这一条不是样板：
    一个无条件补名字的工厂会让**每一条**关于非 ``resolved`` 处置的判据都在
    「带上了主体字段」那一关被拒 —— 于是「归并没有 merged_into」这类测试会
    以正确的异常类型通过，考的却是另一条规则。假绿比红更贵。
    """
    disposition = overrides.get("disposition", Disposition.RESOLVED)
    fields: dict[str, object] = {
        "row_id": row_id,
        "ordinal": ordinal,
        "disposition": disposition,
    }
    if disposition is Disposition.RESOLVED and "resolved_name" not in overrides:
        fields["resolved_name"] = f"Venue {ordinal}"
    fields.update(overrides)
    return RowDisposition(**fields)


def _receipt(**overrides: object) -> SourceReceipt:
    """一份最小的、所有判据都通过的回执：一行、已解决。

    每一格都显式给出。默认值在这里是有害的 —— ``profile_id`` 与 ``batch_id`` 的
    缺省是空串，而空串在报告里读作「上游没给」，于是「测试没设它」与「真实运行里
    上游没给」分不开。

    ``receipt_id`` 从**这份回执实际用的**那三个输入派生（而不是从模块常量），
    否则 ``artifact_sha256`` 一被覆盖，id 就与内容脱钩 —— 于是
    :func:`verify_receipt_id` 的每一条判据都在考一个自洽的假象。
    """
    rows = overrides.pop("dispositions", (_row(),))
    assert isinstance(rows, tuple)
    source_method_id = str(overrides.get("source_method_id", "crawl.site.v1"))
    batch_id = str(overrides.get("batch_id", "batch-1"))
    artifact_sha256 = str(overrides.get("artifact_sha256", _SHA))
    fields: dict[str, object] = {
        "receipt_id": receipt_id_for(
            source_method_id=source_method_id,
            batch_id=batch_id,
            artifact_sha256=artifact_sha256,
            dispositions_digest_value=dispositions_digest(rows),
        ),
        "source_method_id": source_method_id,
        "batch_id": batch_id,
        "artifact_ref": "motherpool-2026-09",
        "artifact_sha256": artifact_sha256,
        "created_at": _CREATED_AT,
        "profile_id": "uk-acme-natural-grass",
        "raw_row_count": len(rows),
        "dispositions": rows,
    }
    fields.update(overrides)
    return SourceReceipt(**fields)


# === 1. 行的身份：``row_id`` ====================================================


def test_row_id_is_a_pure_function_of_its_three_inputs() -> None:
    """同样的三个输入必须给出同一个 id —— 这就是「重放幂等」的全部内容。

    反过来说：任何一点外界输入（时钟、运行 id、集合迭代顺序）都会让重放**多出一批行**，
    而报告上它看起来只是「这一批更大」。
    """
    arguments = {"ordinal": 3, "source_identity": "node/12345", "payload": '{"name":"x"}'}
    assert row_id_for(**arguments) == row_id_for(**arguments)
    assert row_id_for(**arguments) == row_id_for(
        ordinal=3, source_identity="node/12345", payload="ignored-when-identity-is-present"
    ), "有来源标识时 payload 不该参与 —— 否则同一行会因为无关列的空白差异换一个身份"


def test_same_source_identity_at_two_ordinals_is_two_rows() -> None:
    """同一个上游对象在一批里出现两次，是**两行**，各有一个身份。

    这条是 ``merged`` 能表达出来的前提。如果 ``row_id`` 只由来源标识派生，
    这两行会拿到同一个 id —— 于是 ``merged_into`` 只能指向自己，而
    ``RowDisposition`` 明确拒绝自指（``test_merged_into_may_not_point_at_itself``）。
    母池里重复行是常态（同一块场地在两个上游导出里各出现一次），所以这不是边角。
    """
    first = row_id_for(ordinal=0, source_identity="node/12345", payload="")
    second = row_id_for(ordinal=1, source_identity="node/12345", payload="")
    assert first != second


def test_row_id_falls_back_to_payload_when_there_is_no_source_identity() -> None:
    """一份手工 CSV 没有主键 —— 那时身份只能来自**内容**。

    两行逐字相同的行是同一个东西（同一个 id），内容不同就是两行。这条不成立时，
    无主键的母池要么全部塌成一行，要么全部拿不到身份。
    """
    assert row_id_for(ordinal=0, source_identity="", payload='{"a":"1"}') == row_id_for(
        ordinal=0, source_identity="", payload='{"a":"1"}'
    )
    assert row_id_for(ordinal=0, source_identity="", payload='{"a":"1"}') != row_id_for(
        ordinal=0, source_identity="", payload='{"a":"2"}'
    )


def test_row_id_is_a_canonical_id() -> None:
    """它会变成文件名片段，所以必须是 ``slugify`` 的不动点。"""
    generated = row_id_for(ordinal=0, source_identity="node/12345", payload="")
    assert RawCandidateRow(row_id=generated, ordinal=0).row_id == generated


@pytest.mark.parametrize(
    "why, value",
    [
        ("大写字母会被 slugify 折下来，于是两个 id 落到同一个文件上", "Row-ABC"),
        ("含空格 —— 不是规范形式", "row abc"),
        ("首尾空白", " row-abc"),
        ("会被当成路径的斜杠", "row/abc"),
        ("空串", ""),
    ],
)
def test_row_id_rejects_non_canonical_values(why: str, value: str) -> None:
    with pytest.raises(ValueError):
        RawCandidateRow(row_id=value, ordinal=0)


# === 2. 来源身份不是账号身份 ====================================================


def test_source_identity_may_not_look_like_an_email() -> None:
    """一个像邮箱的来源标识会被下游读成**联系方式的依据**。

    它没有出处、没有抓取、没有绑定 —— 而它读起来正是一个可以写进 A/B/C/D 的东西。
    这是本层最贵的一类静默错误，所以在入口处就拒。
    """
    with pytest.raises(ValueError, match="不能像一个邮箱"):
        RawCandidateRow(row_id="row-1", ordinal=0, source_identity="info@client42.example.org")


@pytest.mark.parametrize(
    "why, value",
    [
        ("带空白的标识在报告里与「两个不同的标识被写成一行」分不开", "node 12345"),
        ("首尾空白", " node/12345"),
        ("换行", "node/\n12345"),
    ],
)
def test_source_identity_rejects_whitespace(why: str, value: str) -> None:
    with pytest.raises(ValueError, match="空白"):
        RawCandidateRow(row_id="row-1", ordinal=0, source_identity=value)


def test_source_identity_may_be_empty() -> None:
    """很多母池根本没有稳定主键。空是**缺席**，不是「上游说它没有标识」。"""
    assert RawCandidateRow(row_id="row-1", ordinal=0).source_identity == ""


def test_source_identity_is_not_a_filename() -> None:
    """来源标识用的是上游的字符串，**永远不变成**本仓的文件名。

    所以 ``:`` 与 ``/`` 在它里面合法，而在 ``row_id`` 里非法 —— 这不是两条打架的
    规则，是本模块的要点。它成立时，「上游的一个标识」与「本仓的一个文件」之间
    就不存在需要人记住的对应关系。
    """
    row = RawCandidateRow(row_id="row-1", ordinal=0, source_identity="node/12345")
    assert row.source_identity == "node/12345"


# === 3. 处置与它带的字段必须自洽 ================================================


def test_resolved_without_a_name_is_unrepresentable() -> None:
    """**只有标识的行不是一个主体。**

    这正是 Member-C 说的那类行（``sources/member-c-private`` pos30：「纯 osm 只有
    代码 id，很少能跑到具体场地名称」），也正是 2026-09-23 那次事故的形状：
    一个拿不出指称对象的行被当成可以开工的主体发出去，worker 只能拿标识去检索 ——
    9 次查询落在无关的同名物上，而交回的结算与「这个主体真的没有公开信息」
    不可区分。

    这样的行的处置应该是 ``pending``，理由里写清楚缺什么。
    """
    with pytest.raises(ValueError, match="没有 resolved_name"):
        RowDisposition(
            row_id="row-1",
            ordinal=0,
            disposition=Disposition.RESOLVED,
            resolved_location="Devon",
        )


@pytest.mark.parametrize(
    "why, extra",
    [
        ("处置是待补全，名字那格却有内容 —— 下游会读成一个可以开工的主体", {"resolved_name": "X"}),
        ("同上，地点", {"resolved_location": "Devon"}),
        ("同上，上游说的域名", {"claimed_official_domain": "example.co.uk"}),
    ],
)
def test_non_resolved_rows_carry_no_subject_fields(why: str, extra: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="带上了主体字段"):
        _row(disposition=Disposition.PENDING, reason="缺名字", **extra)


@pytest.mark.parametrize(
    "why, disposition",
    [
        ("归并 —— 少了的那一行去哪了，答不出来", Disposition.MERGED),
        ("排除 —— 按什么排除的，事后答不出来", Disposition.EXCLUDED),
        ("待补全 —— 它到底缺什么", Disposition.PENDING),
        ("读失败 —— 它是需要人看，还是本来就该丢", Disposition.FAILED),
    ],
)
def test_every_non_resolved_row_says_why(why: str, disposition: Disposition) -> None:
    """一条说不出理由的处置，与「这一行被静默丢掉了」在报告里长得一样。

    而母池的失败形态正是「少了的那几行没人说得清去哪了」。
    """
    with pytest.raises(ValueError, match="没有理由"):
        _row(disposition=disposition)


def test_merged_must_name_the_row_it_folded_into() -> None:
    with pytest.raises(ValueError, match="没有 merged_into"):
        _row(disposition=Disposition.MERGED, reason="同名同地点")


def test_merged_into_may_not_point_at_itself() -> None:
    """自指不是归并，是一条循环 —— 报告上它看起来像一次正常的合并。"""
    with pytest.raises(ValueError, match="指向它自己"):
        _row(
            row_id="row-1",
            disposition=Disposition.MERGED,
            reason="同名同地点",
            merged_into="row-1",
        )


def test_merged_into_must_be_a_canonical_row_id() -> None:
    with pytest.raises(ValueError):
        _row(
            disposition=Disposition.MERGED,
            reason="同名同地点",
            merged_into="Row-2",  # 大写
        )


@pytest.mark.parametrize(
    "why, disposition, extra",
    [
        ("resolved 不该说它归并到了谁", Disposition.RESOLVED, {"merged_into": "row-2"}),
        ("pending 同上", Disposition.PENDING, {"reason": "缺名字", "merged_into": "row-2"}),
        ("resolved 不该报一个排除项 id", Disposition.RESOLVED, {"exclusion_ids": ("golf",)}),
        (
            "pending 同上",
            Disposition.PENDING,
            {"reason": "缺名字", "exclusion_ids": ("golf",)},
        ),
    ],
)
def test_disposition_specific_fields_are_exclusive(
    why: str, disposition: Disposition, extra: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        _row(disposition=disposition, **extra)


def test_excluded_must_name_the_declared_exclusions() -> None:
    """排除**必须**命中 profile 声明过的那一项（或那几项）。

    引擎自己一条排除规则都不认识（``tests/test_no_hardcoded_market_knowledge.py``
    对整包强制执行），所以「按什么排除的」只能由这一格回答。它空了，事后既答不出
    「按什么排除」，也答不出「改了排除规则之后要重跑哪些」。
    """
    with pytest.raises(ValueError, match="没有 exclusion_ids"):
        _row(disposition=Disposition.EXCLUDED, reason="这不是天然草")


def test_a_row_may_hit_two_declared_exclusions_at_once() -> None:
    """一行可以同时命中两条排除项，而且**两条都要留下来**。

    这正是一个元组而不是一个字符串的理由：一块场地既可能是人工草皮、又可能是
    某项活动的场地，而「取第一条」会让报告上剩下那一条消失 —— 这一层的全部工作
    就是让每一个去向都看得见。事后要靠这张表回答「改了哪条规则要重跑哪些」，
    而被吞掉的那一条永远重跑不到。
    """
    row = _row(
        disposition=Disposition.EXCLUDED,
        reason="既不是天然草，也不在这项运动的场地范围里",
        exclusion_ids=("artificial_grass", "out_of_scope_sport"),
    )
    assert row.exclusion_ids == ("artificial_grass", "out_of_scope_sport")


def test_claimed_official_domain_must_be_a_bare_host() -> None:
    """带 scheme 的值在这里就报错。

    下游拿它当主机去比较（「上游说的那个」对「我们证得的那个」），而
    ``https://a.co.uk`` 与 ``a.co.uk`` 永远不相等 —— 差别看不出来，表现为
    「两个域名不是同一个」，而不是一次报错。
    """
    with pytest.raises(ValueError):
        _row(claimed_official_domain="https://example.co.uk")


def test_claimed_official_domain_may_be_empty() -> None:
    """没有域名的身份发现照样能做（先搜名字、再确认官网）。空是缺席。

    这一格空**不是**「这个组织没有官网」—— 那是 ``domain_verdict=absent``。
    它说的是「这一份导出那一列没写」。
    """
    assert _row().claimed_official_domain == ""
    assert _row().domain_verdict is DomainVerdict.UNKNOWN


# === 4. 回执：每一行恰好一个处置 ================================================


def test_a_receipt_reproduced_from_its_own_fields_verifies() -> None:
    assert verify_receipt_id(_receipt()) is True


def test_tampered_dispositions_break_the_receipt_id() -> None:
    """把一行的处置改掉、``receipt_id`` 不动 —— 这份回执必须对不上自己。

    它挡的是「有人手工改了盘上的回执」。改完 ``verify_receipt_id`` 为假，
    于是「这份回执没被动过」这件事有一个不需要信任任何人的答案。
    """
    original = _receipt()
    rows = (
        _row(row_id="row-1", ordinal=0, resolved_name="改了名字"),
    )
    forged = original.model_copy(update={"dispositions": rows})
    assert verify_receipt_id(forged) is False


def test_a_receipt_with_no_raw_rows_is_unrepresentable() -> None:
    """**空池不是成功。**

    一份 0 行的母池与一份「读进 300 行、一行都没成」在报告里必须长得不一样，
    而后者才是有信息量的那一种。``raw_row_count >= 1`` 让前者构造不出来。
    """
    with pytest.raises(ValueError):
        _receipt(raw_row_count=0, dispositions=())


def test_a_dropped_row_is_unrepresentable() -> None:
    """声明读了 3 行、却只给出 2 条处置 —— 构造不出来。

    丢行是最贵的那一类失败：剩下的行会被读成**全池**，于是「母池覆盖率」的分母
    从一开始就是错的，而报告上只显示「候选更少」。
    """
    rows = tuple(_row(row_id=f"row-{i}", ordinal=i) for i in range(2))
    with pytest.raises(ValueError, match="却给出"):
        _receipt(raw_row_count=3, dispositions=rows)


@pytest.mark.parametrize(
    "why, ordinals",
    [
        ("序号重复 —— 「这一行对应工件里的哪一行」没有答案", (0, 0)),
        ("序号跳号 —— 中间那一行没有处置", (0, 2)),
        ("序号不从 0 开始", (1, 2)),
    ],
)
def test_ordinals_must_be_exactly_a_permutation(why: str, ordinals: tuple[int, ...]) -> None:
    """序号是回执与原始工件之间**唯一**的对照键（名字与标识都可能重复）。

    它的排列性就是「每一原始行都有处置」这句话本身。
    """
    rows = tuple(_row(row_id=f"row-{i}", ordinal=ordinal) for i, ordinal in enumerate(ordinals))
    with pytest.raises(ValueError, match="排列"):
        _receipt(raw_row_count=len(rows), dispositions=rows)


def test_two_rows_may_not_share_one_row_id() -> None:
    """``merged_into`` 按 row_id 指向另一行；重复的 id 会让一次归并同时指向两行。

    于是「归并到了哪一个」取决于谁先读 —— 那是一次不可复现的报告。
    """
    rows = (
        _row(row_id="row-same", ordinal=0),
        _row(row_id="row-same", ordinal=1, resolved_name="别的场地"),
    )
    with pytest.raises(ValueError, match="不止一次"):
        _receipt(raw_row_count=2, dispositions=rows)


def test_an_unregistered_source_method_is_refused_by_the_registry() -> None:
    """**失败关闭**：未注册的来源 id 直接抛 ``ConfigError``。

    它没有走 ``ValidationError`` 那条通道 —— 这条规则的权威在
    ``domain/sources.py``，那里用的是 ``ConfigError``。两处各写一遍会让
    「加一个来源要走哪里」有两个答案。

    而它为什么必须失败关闭：一个未注册的 id 与一个拼错的已注册 id 在报告里
    长得一样，而前者会让 SOP L82 那句「搜索结果只能是补充证据、不能当全量母池」
    失去依据 —— 那一行 ``source_family`` 算不出来。
    """
    with pytest.raises(ConfigError):
        _receipt(source_method_id="search.engine.v99")


def test_an_unknown_receipt_shape_version_is_refused() -> None:
    """回执是落盘后被读回的物件 —— 用一套不认识的字段去读它，失败形态不是报错。

    是把空处读成「上游没给」。
    """
    with pytest.raises(ValueError, match="形状版本"):
        _receipt(version="v99")


def test_the_default_version_is_the_one_this_code_writes() -> None:
    assert _receipt().version == RECEIPT_VERSION


@pytest.mark.parametrize(
    "why, value",
    [
        ("长度不对", "abc"),
        ("含十六进制以外的字符", "z" * 64),
        ("空串", ""),
    ],
)
def test_artifact_sha256_must_be_a_digest(why: str, value: str) -> None:
    """它是「读的是哪些字节」这个问题的**全部**答案。

    一个形状不对的值在那里等于没有答案 —— 而在报告上它会看起来像有一个。
    """
    with pytest.raises(ValueError):
        _receipt(artifact_sha256=value)


def test_a_naive_created_at_is_refused() -> None:
    """它要与 ``cutoff`` 比先后，而 naive 比较是「按墙上时间猜」——猜错不会报错。"""
    with pytest.raises(ValueError, match="时区"):
        _receipt(created_at=datetime(2026, 9, 23, 10, 0, 0))


@pytest.mark.parametrize("field", ["receipt_id", "artifact_ref"])
def test_filenames_are_canonical_ids(field: str) -> None:
    """这两个都会变成文件名片段（``<receipt_id>.receipt.json``）。"""
    with pytest.raises(ValueError):
        _receipt(**{field: "Not-Canonical"})


@pytest.mark.parametrize("field", ["batch_id", "profile_id"])
def test_free_labels_reject_path_characters(field: str) -> None:
    """标签不进文件名，所以走松的那一条 —— 但仍不是一段可以随便写的文本。"""
    with pytest.raises(ValueError):
        _receipt(**{field: "a/b"})


# === 5. 派生：一个事实只落一次 ==================================================


def _mixed_receipt() -> SourceReceipt:
    """一份五种处置齐全的回执。**没有**第二个地方存这些计数 —— 全部派生。"""
    rows = (
        _row(row_id="row-0", ordinal=0, resolved_name="Venue A", claimed_official_domain="a.co.uk"),
        _row(
            row_id="row-1",
            ordinal=1,
            disposition=Disposition.MERGED,
            reason="与第 0 行同名同地点",
            merged_into="row-0",
        ),
        _row(
            row_id="row-2",
            ordinal=2,
            disposition=Disposition.EXCLUDED,
            reason="命中 profile 声明的排除项",
            exclusion_ids=("artificial_grass",),
        ),
        _row(
            row_id="row-3",
            ordinal=3,
            disposition=Disposition.PENDING,
            reason="只有来源标识，名字那一格是空的",
        ),
        _row(
            row_id="row-4",
            ordinal=4,
            disposition=Disposition.FAILED,
            reason="第三列缺失，这一行读不出来",
        ),
    )
    return _receipt(raw_row_count=5, dispositions=rows)


def test_counts_are_derived_and_add_up_to_the_raw_row_count() -> None:
    """五个计数之和必须等于 ``raw_row_count`` —— 这就是那条不变量的读数。

    它是**派生**的：同一件事存两遍，漂移的那一次没有人能看出哪个是错的。
    """
    receipt = _mixed_receipt()
    assert receipt.counts == {
        "resolved": 1,
        "merged": 1,
        "excluded": 1,
        "pending": 1,
        "failed": 1,
    }
    assert sum(receipt.counts.values()) == receipt.raw_row_count


def test_counts_line_names_every_disposition_including_the_zeroes() -> None:
    """报告那一行必须逐项列出，**不判成败**。

    「成功了 N 行」这种说法在这里是不诚实的：``pending`` 不是失败，但它也绝不是
    产出 —— 它是一次**还没发生**的补全。
    """
    receipt = _receipt()  # 一行、已解决
    assert receipt.counts_line == (
        "raw=1 resolved=1 merged=0 excluded=0 pending=0 failed=0"
        " resolved_rows=1 distinct_entities=1"
    )


def test_the_row_count_and_the_entity_count_are_two_numbers() -> None:
    """**25 行进来不等于 25 个东西。**

    §3.1 逐字要求「归并后报唯一实体数，不与行数混用」。这里用一份三行同名同地点
    的池子把那条要求钉住：三行都是 ``resolved``（引擎不在名字上归并 —— 那是一个
    判断，这一层拿不出证据），但读的人要能看出它说的是**一个**东西。

    两个数都在 ``counts_line`` 上，且**并列**出现 —— 合成一个「有效 3 条」正是
    这一行最容易被读错的方式。
    """
    rows = (
        _row(row_id="row-0", ordinal=0, resolved_name="Village Hall", resolved_location="Devon"),
        _row(row_id="row-1", ordinal=1, resolved_name="Village Hall", resolved_location="Devon"),
        _row(row_id="row-2", ordinal=2, resolved_name="Village Hall", resolved_location="Devon"),
    )
    receipt = _receipt(dispositions=rows)

    assert receipt.counts[Disposition.RESOLVED.value] == 3, "行数回答「读进去多少条」"
    assert receipt.resolved_row_count == 3
    assert receipt.distinct_resolved_count == 1, "这个数回答「里面有几个东西」"


def test_two_places_with_one_name_are_two_entities() -> None:
    """去重键是 ``(名字, 地点)`` —— 同名不同地点是两个单位，**不得**算成一个。

    英国一个村子里两个不同单位同名是常见的（``application/motherpool.py`` 的
    docstring 逐字写了这件事）。拿名字当键会低估，而低估的后果是「这批池子看起来
    比实际小」，比高估更难被发现。
    """
    rows = (
        _row(row_id="row-0", ordinal=0, resolved_name="Village Hall", resolved_location="Devon"),
        _row(row_id="row-1", ordinal=1, resolved_name="Village Hall", resolved_location="Cornwall"),
    )

    assert _receipt(dispositions=rows).distinct_resolved_count == 2


def test_resolved_rows_come_back_in_ordinal_order() -> None:
    """顺序有意义：它决定 ``ordinal``，因而决定 ``work_item_id``。"""
    rows = (
        _row(row_id="row-0", ordinal=0, resolved_name="A"),
        _row(
            row_id="row-1",
            ordinal=1,
            disposition=Disposition.PENDING,
            reason="缺名字",
        ),
        _row(row_id="row-2", ordinal=2, resolved_name="C"),
    )
    receipt = _receipt(raw_row_count=3, dispositions=rows)
    assert [item.ordinal for item in receipt.resolved_rows] == [0, 2]
    assert [item.resolved_name for item in receipt.resolved_rows] == ["A", "C"]


def test_a_receipt_with_a_resolved_row_has_no_blocker() -> None:
    assert _receipt().blocker == ""


def test_a_receipt_with_no_resolved_row_has_a_blocker_that_counts_the_rows() -> None:
    """**一份没有任何可用主体的母池不是「已经做完」—— 它什么都没跑。**

    而这条理由必须说出那一批行到底怎么了，否则「0 个可用主体」与「上游交了一份
    空文件」在调用点长得一样 —— 后者该报错，前者该被记录并开工下一步。
    """
    rows = (
        _row(
            row_id="row-0",
            ordinal=0,
            disposition=Disposition.PENDING,
            reason="只有来源标识，名字那一格是空的",
        ),
        _row(
            row_id="row-1",
            ordinal=1,
            disposition=Disposition.PENDING,
            reason="同上",
        ),
    )
    receipt = _receipt(raw_row_count=2, dispositions=rows)
    blocker = receipt.blocker
    assert blocker, "没有 resolved 行时必须有阻塞理由"
    assert "raw=2" in blocker
    assert "pending=2" in blocker
    assert "身份补全" in blocker, "pending 占多数时应当点名缺的是哪一步"


def test_the_blocker_is_not_a_verdict_on_bad_rows() -> None:
    """``failed`` / ``excluded`` / ``pending`` 的行**不是**阻塞。

    它们是被记录下来的事实。把它们算成阻塞，等于说「一份有 5 行坏数据的 300 行
    母池不能开工」—— 而正确的处置是开工，并把这 5 行留在报告上。
    """
    assert _mixed_receipt().blocker == ""


# === 6. 回执 id：内容寻址 =======================================================


def test_receipt_id_is_stable_for_the_same_content() -> None:
    """同样输入 + 同样规则 → 同一个 id → 同一个文件 → **重放不新建任何东西**。"""
    assert _receipt().receipt_id == _receipt().receipt_id


def test_a_rules_change_yields_a_different_receipt_id() -> None:
    """同样输入 + **改过的规则** → 另一个 id → 另一个文件，两份都留在盘上。

    少了这条，「这一批行被重新处置过一次、两次的结论不一样」在盘上不留痕迹 ——
    而它是审计里最先要问的那一个问题。把规则版本写成一个字段也能做到同样的事，
    但那需要维护一张版本表；处置摘要不需要任何约定：改没改过，比对两个 id 就知道。
    """
    before = dispositions_digest((_row(row_id="row-0", ordinal=0, resolved_name="A"),))
    after = dispositions_digest(
        (
            _row(
                row_id="row-0",
                ordinal=0,
                disposition=Disposition.PENDING,
                reason="补全规则改了，这一行现在只剩标识",
            ),
        )
    )
    assert before != after
    assert receipt_id_for(
        source_method_id="crawl.site.v1",
        batch_id="batch-1",
        artifact_sha256=_SHA,
        dispositions_digest_value=before,
    ) != receipt_id_for(
        source_method_id="crawl.site.v1",
        batch_id="batch-1",
        artifact_sha256=_SHA,
        dispositions_digest_value=after,
    )


def test_the_dispositions_digest_does_not_depend_on_container_order() -> None:
    """位置由 ``ordinal`` 那一格承载，不由元组的迭代顺序承载。

    「这一批的位置变了」唯一的表达方式是让 ``ordinal`` 变 —— 而一个从 ``set``
    或者从另一次读取顺序不同的列表进来的调用，说的还是同一件事，不该算出另一个
    id（那会让一次重放被读成一批新行）。
    """
    first = _row(row_id="row-0", ordinal=0, resolved_name="A")
    second = _row(row_id="row-1", ordinal=1, resolved_name="B")
    assert dispositions_digest((first, second)) == dispositions_digest((second, first))


def test_which_row_got_which_disposition_is_in_the_digest() -> None:
    """换掉**哪一行**得到了什么处置 —— 摘要必须变。

    这是上一条的反面，两条一起才说得清楚：位置靠 ``ordinal`` 进散列，而不是靠
    容器顺序。少了这一条，「摘要对顺序不敏感」这个性质会把「rows 0 和 1 的结论
    对调了」也一并吃掉。
    """
    here = dispositions_digest(
        (
            _row(row_id="row-0", ordinal=0, resolved_name="A"),
            _row(
                row_id="row-1",
                ordinal=1,
                disposition=Disposition.PENDING,
                reason="缺名字",
            ),
        )
    )
    there = dispositions_digest(
        (
            _row(
                row_id="row-0",
                ordinal=0,
                disposition=Disposition.PENDING,
                reason="缺名字",
            ),
            _row(row_id="row-1", ordinal=1, resolved_name="A"),
        )
    )
    assert here != there


def test_a_different_artifact_yields_a_different_receipt_id() -> None:
    """文件同名而内容换过，是**两份**工件 —— 差别（一个字符）在报告上看不出来。"""
    assert _receipt(artifact_sha256="a" * 64).receipt_id != _receipt(
        artifact_sha256="b" * 64
    ).receipt_id


def test_receipt_id_is_a_canonical_id() -> None:
    """它变成 ``<receipt_id>.receipt.json`` —— 需要规范化的 id 会与另一个 id 撞文件。"""
    assert _receipt().receipt_id.startswith("receipt-")
