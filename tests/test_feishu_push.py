"""飞书写入路径的**隔离**契约/集成测试 —— 每次通过都是一次 ``sandbox_pass``。

## 这些测试不是真实厂商的 PASS，测试名里写着这件事

§9：隔离环境的通过记为 ``sandbox_pass``，不得伪装成真实厂商 PASS。本文件里带
``sandbox_pass`` 的那几条跑的是**完整**一条写入路径（授权 → 凭据 → 记录体 →
令牌 → 查 → 写 → 读回核对），而它的远端是一个同进程的 ``httpx.MockTransport``，
也就是一台**有状态的假表**：一次网络都不发。真实飞书写入是 **BLOCKED** 的
（缺业务授权 + 缺凭据 + 这条路从未对厂商执行过一次），所以「这些测试全绿」
与「飞书那头能写」是两件事 —— 把它们读成同一件事，正是这一批测试要防的失败。

## 这批测试钉住的四件事，各有一节

* **幂等**：同一件事推两次不得产生两行 —— 去重键是 ``handoff_id``，
  也就是**我们给的键**，不是提供方的承诺（提供方行为本轮未经核实）；
* **读回**：行 id、版本、条数。写完必须再查一次，**条数不等于预期就是一次失败**，
  不是一次成功；
* **授权**：默认关、env 打不开、未授权时拒写，且**一次 HTTP 都不发**
  （用一个「被调用就炸」的传输替身证明这一点）；
* **不截断**：回信正文逐字进记录，放不下就拒写并说清楚超了多少。

夹具里的每一条记录都是**编的**：没有真实租户、没有凭据、没有真实收件人。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from marketops.adapters.feishu import (
    ADAPTER_STATE,
    BITABLE_HANDOFF_FIELD_MAP,
    BITABLE_HANDOFF_KEY_COLUMN,
    BITABLE_REPLY_BODY_CHARS_COLUMN,
    LIVE_WRITE_BLOCKERS,
    LIVE_WRITE_STATE,
    MAX_BITABLE_TEXT_CHARS,
    RECORD_KIND_STATE,
    WRITE_STEPS,
    BitableWriteReceipt,
    FeishuBitableClient,
    oversized_text_fields,
    to_bitable_handoff_record,
)
from marketops.config import Settings
from tests.conftest import DEFAULT_PROFILE
from marketops.domain.outreach import HandoffState, SalesHandoff

#: 替身认的两个端点尾段。
TOKEN_SUFFIX = "/tenant_access_token/internal"
SEARCH_SUFFIX = "/records/search"

#: 替身回的 tenant_access_token。**它不是凭据**：它只活在这个文件里，
#: 永不进入任何真实系统，也永不与任何真实的 secret 混用。
FAKE_TOKEN = "t-fake-token-for-the-transport-double"

#: 编的 app_secret。用它的地方有两处：构造 Settings，以及一条「提供方回显了它、
#: 而我们把它脱敏掉」的判据。**真值在 vault 里，不在这个文件里。**
FAKE_SECRET = "fake-app-secret-not-a-credential"


def _configured(data_root: Path, **overrides: Any) -> Settings:
    """一个**有凭据**的 ``Settings``。凭据全部是编的 —— 见文件 docstring。

    写入授权刻意**不由参数打开**：它是模块级常量，测试要打开它只能 monkeypatch
    那个常量（见 ``authorized`` 夹具），而那个动作在测试里留痕 —— 它是一次
    「我在伪造部署开关」的显式声明。
    """
    payload: dict[str, Any] = {
        "data_root": data_root / "runtime",
        "profiles_dir": data_root / "profiles",
        "profile": "uk-acme-natural-grass",
        "feishu_app_id": "cli_fake_app_id",
        "feishu_app_secret": FAKE_SECRET,
        "feishu_bitable_app_token": "fake_app_token",
        "feishu_bitable_table_id": "tblFake",
    }
    payload.update(overrides)
    return Settings(**payload)


@pytest.fixture
def authorized(monkeypatch) -> None:
    """把**部署开关**伪造为开 —— 本文件唯一一条让写入路径走完的办法。

    ``Settings.feishu_write_authorized`` 是一个返回模块级常量的只读属性，刻意没有
    setter、不读环境变量、也不是任何函数的参数。要覆盖「授权在位」那条路，只能改
    那个常量本身；而**改得动模块常量**与「生产里能被打开」是两回事 ——
    ``test_sandbox_pass_authorization_cannot_be_opened_by_env_or_argument`` 是
    另一半判据。
    """
    import marketops.config as config

    monkeypatch.setattr(config, "_FEISHU_WRITE_AUTHORIZED", True)


def make_handoff(**overrides: Any) -> SalesHandoff:
    payload: dict[str, Any] = {
        "handoff_id": "handoff-0001",
        "workspace": "ws-1",
        "scene_id": "scene-1",
        "campaign_id": "campaign-1",
        "account_id": "account-1",
        "route_ref": "route-1",
        "event_id": "event-1",
        "recipient": "grounds@example.co.uk",
        "reply_body": "  Thank you — yes, we look after that site.\n\nRegards,\nA. Grounds  ",
        "evidence_ids": ("evidence-1",),
        "created_at": "2026-09-20T09:00:00+00:00",
    }
    payload.update(overrides)
    return SalesHandoff(**payload)


# --------------------------------------------------------------------------
# 传输替身：一台**同进程**的假表
# --------------------------------------------------------------------------


class FakeBitable:
    """一台记着「表里现在有什么」的假表。**不是 mock —— 它有状态。**

    有状态是必要的：幂等这个性质只能在「写两次、表里仍是一行」上被观察，
    而一个每次都回同样答案的 mock 对它一个字都说不出。

    Args:
        total_after_write: 写完之后的检索回几个条数。``None`` = 如实回（默认）。
            传 2 用来制造「写进去了，但读回是两行」——那是必须有失败回执的情形。
        total_missing: 检索响应里**不带** ``total``。用来制造「读不出条数」。
        row_id_after_write: 写完之后的检索回哪个行 id。``None`` = 如实回。
            传别的值用来制造「条数对、行却换了一行」。
        version: 记录对象上回不回 ``version``。``None`` = 不回（提供方没给版本号）。
    """

    def __init__(
        self,
        *,
        total_after_write: int | None = None,
        total_missing: bool = False,
        row_id_after_write: str | None = None,
        version: str | None = "17",
        write_row_id: str = "recFake0001",
    ) -> None:
        self.rows: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.writes = 0
        self.total_after_write = total_after_write
        self.total_missing = total_missing
        self.row_id_after_write = row_id_after_write
        self.version = version
        self.write_row_id = write_row_id

    # --- 请求入口 ---------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url.path)))
        path = request.url.path
        if path.endswith(TOKEN_SUFFIX):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": FAKE_TOKEN,
                    "expire": 7200,
                },
            )
        if path.endswith(SEARCH_SUFFIX):
            return self._search(request)
        if request.method == "POST":
            return self._create(request)
        if request.method == "PUT":
            return self._update(request)
        return httpx.Response(405, json={"code": 99, "msg": "method not allowed"})

    # --- 表的行为 ---------------------------------------------------------
    def _search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wanted = body["filter"]["conditions"][0]["value"][0]
        matched = [
            row for row in self.rows if row["fields"].get(BITABLE_HANDOFF_KEY_COLUMN) == wanted
        ]
        total: int | None = len(matched)
        if self.writes and self.total_after_write is not None:
            total = self.total_after_write
        items = [self._as_item(row) for row in matched[:2]]
        if self.writes and self.row_id_after_write is not None and items:
            items[0] = {**items[0], "record_id": self.row_id_after_write}
        data: dict[str, Any] = {"items": items}
        if not self.total_missing:
            data["total"] = total
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": data})

    def _as_item(self, row: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {"record_id": row["record_id"], "fields": row["fields"]}
        if self.version is not None:
            item["version"] = self.version
        return item

    def _create(self, request: httpx.Request) -> httpx.Response:
        row = {"record_id": self.write_row_id, "fields": json.loads(request.content)["fields"]}
        self.rows.append(row)
        self.writes += 1
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"record": self._as_item(row)}})

    def _update(self, request: httpx.Request) -> httpx.Response:
        row_id = str(request.url.path).rsplit("/", 1)[-1]
        for row in self.rows:
            if row["record_id"] == row_id:
                row["fields"] = json.loads(request.content)["fields"]
                self.writes += 1
                return httpx.Response(
                    200, json={"code": 0, "msg": "ok", "data": {"record": self._as_item(row)}}
                )
        return httpx.Response(404, json={"code": 99, "msg": "record not found"})


def _client(fake: FakeBitable, settings: Settings) -> FeishuBitableClient:
    """把假表接进客户端。**注入点就是「不许发网络请求」那条判据所在。**"""
    return FeishuBitableClient(
        settings, client=httpx.Client(transport=httpx.MockTransport(fake.handler))
    )


class _ExplodingTransport(httpx.BaseTransport):
    """一个**被调用就炸**的传输替身。

    它存在的唯一理由是让「未授权时一次 HTTP 都不发」这句话有判别力：
    一个安静的替身（回 200 或者回 500）都区分不出「没发」与「发了但被拒」。
    """

    def __init__(self) -> None:
        self.hits: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(str(request.url))
        raise AssertionError(
            "这一条路径**不得**发出任何 HTTP 请求，而它发了一次："
            f"{request.method} {request.url}"
        )


# --------------------------------------------------------------------------
# 1. 结构化映射：交接那一条（纯函数，现在就能测）
# --------------------------------------------------------------------------


def test_handoff_field_map_covers_every_handoff_field() -> None:
    """映射必须覆盖 SalesHandoff 的每一个字段，否则接线时会有字段没人知道放哪。"""
    assert set(BITABLE_HANDOFF_FIELD_MAP) == set(SalesHandoff.model_fields)


def test_handoff_record_carries_the_reply_body_verbatim() -> None:
    """交接的核心就是让销售读原文：正文**逐字**进记录。"""
    body = "  Thank you — we do look after that site.\n\nRegards,\nA. Grounds  "
    record = to_bitable_handoff_record(make_handoff(reply_body=body))

    assert record[BITABLE_HANDOFF_FIELD_MAP["reply_body"]] == body
    assert record[BITABLE_REPLY_BODY_CHARS_COLUMN] == len(body)


def test_handoff_record_holds_the_business_key() -> None:
    """去重的键写进记录的一列 —— 没有它，查重就没有可查的东西。"""
    record = to_bitable_handoff_record(make_handoff())
    assert record[BITABLE_HANDOFF_KEY_COLUMN] == "handoff-0001"


def test_handoff_record_has_no_secret_like_keys() -> None:
    record = to_bitable_handoff_record(make_handoff())
    lowered = " ".join(record).lower()
    for needle in ("secret", "token", "password", "api_key"):
        assert needle not in lowered


def test_the_text_ceiling_is_the_domain_ceiling() -> None:
    """外面那一侧的上限不得比里面那一侧紧，也不得比它松。

    更紧的守门值会拒掉一封在本仓合法的信（而拒写看起来像「集成坏了」）；
    更松的那个则让「提供方悄悄截断」重新变成一个可能的失败。
    """
    from marketops.domain.outreach import MAX_BODY_CHARS

    assert MAX_BITABLE_TEXT_CHARS == MAX_BODY_CHARS


def test_the_oversize_check_only_looks_at_text() -> None:
    """``None`` / ``False`` 不是文本，把它们读成长度会造出假的超限。"""
    assert oversized_text_fields({"A": None, "B": True, "C": 12}) == ()


def test_column_names_live_in_exactly_one_file() -> None:
    """列名只在 ``bitable.py`` 里写一次 —— **客户端里一个字面量列名都没有**。

    散出去的那一份的失效方式不报错：改列名只改了一处，另一处照旧写进一列没人看的
    地方，而每一次写入都返回 2xx。所以这一条扫源码而不是扫行为 —— 行为在
    「只是写到别处」时和成功长得一样。
    """
    from marketops.adapters.feishu import bitable as mapping

    columns = (
        set(mapping.BITABLE_FIELD_MAP.values())
        | set(mapping.BITABLE_OPPORTUNITY_FIELD_MAP.values())
        | set(mapping.BITABLE_HANDOFF_FIELD_MAP.values())
        | {mapping.BITABLE_REPLY_BODY_CHARS_COLUMN}
    )
    package = Path(mapping.__file__).parent
    for name in ("client.py", "__init__.py"):
        source = (package / name).read_text(encoding="utf-8")
        found = sorted(column for column in columns if f'"{column}"' in source)
        assert not found, f"{name} 里出现了字面量列名：{found} —— 列名只在 bitable.py 里写"


# --------------------------------------------------------------------------
# 2 + 3. 幂等写入与读回：**这是重点**（每条都是 sandbox_pass）
# --------------------------------------------------------------------------


def test_sandbox_pass_first_push_creates_exactly_one_row(authorized, tmp_path: Path) -> None:
    """sandbox_pass：写入 → 读回 → 核对条数、行 id、版本。远端是同进程的假表。

    这一次是**新建**（表里原本 0 行），回执里三样齐全。
    """
    fake = FakeBitable()
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is True, receipt.detail
    assert receipt.created is True
    assert receipt.count_before == 0
    assert receipt.count_after == 1
    assert receipt.expected_count == 1
    assert receipt.row_id == "recFake0001"
    assert receipt.version == "17"
    assert receipt.step == "readback"
    assert len(fake.rows) == 1


def test_sandbox_pass_pushing_the_same_handoff_twice_leaves_one_row(
    authorized, tmp_path: Path
) -> None:
    """sandbox_pass：幂等。**去重的键是 ``handoff_id``（我们给的），不是提供方的承诺。**

    推两次：第二次落在**更新**那一行上，而不是新建第二行。表里始终一行。
    """
    fake = FakeBitable()
    client = _client(fake, _configured(tmp_path))

    first = client.push_handoff(make_handoff(reply_body="第一版"))
    second = client.push_handoff(make_handoff(reply_body="第二版"))

    assert first.created is True
    assert second.created is False, "同一件事推第二次必须落到更新上"
    assert second.business_key == "handoff-0001"
    assert second.key_column == BITABLE_HANDOFF_KEY_COLUMN
    assert len(fake.rows) == 1
    assert fake.rows[0]["fields"][BITABLE_HANDOFF_FIELD_MAP["reply_body"]] == "第二版"


def test_sandbox_pass_a_second_row_for_the_same_key_refuses_to_write(
    authorized, tmp_path: Path
) -> None:
    """表里已经有两行了：**拒写**，并把读到的东西说出来。

    这里挡的不是「多写一行」——是「表已经坏了，而一次看起来很成功的写入会把这个
    事实盖过去」。回执里必须读得出「读到 2 行」。
    """
    fake = FakeBitable()
    fake.rows = [
        {"record_id": "recA", "fields": {BITABLE_HANDOFF_KEY_COLUMN: "handoff-0001"}},
        {"record_id": "recB", "fields": {BITABLE_HANDOFF_KEY_COLUMN: "handoff-0001"}},
    ]
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.step == "query"
    assert receipt.reason == "duplicate_rows_for_business_key"
    assert receipt.count_before == 2
    assert len(fake.rows) == 2, "拒写之后表里不得多出行"


def test_sandbox_pass_readback_count_mismatch_is_a_failure_not_a_success(
    authorized, tmp_path: Path
) -> None:
    """**写完再查，条数不等于预期 → 失败。** 这就是「写了就报成功」那一格。

    HTTP 那一侧全绿（写入返回 200），而读回是 2 行：这一次**不算成功**。
    """
    fake = FakeBitable(total_after_write=2)
    fake.rows = [{"record_id": "recFake0001", "fields": {}}]
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.step == "readback"
    assert receipt.reason == "readback_count_mismatch"
    assert receipt.count_after == 2
    assert receipt.expected_count == 1
    assert receipt.row_id == "recFake0001", "回执仍要说得出「我们写到的那一行是哪一行」"


def test_sandbox_pass_readback_row_id_mismatch_is_a_failure(authorized, tmp_path: Path) -> None:
    """条数对得上、行却换了 —— 这比条数不对更危险：去重的那个键在这张表上不唯一。"""
    fake = FakeBitable(row_id_after_write="recSomebodyElse")
    fake.rows = [{"record_id": "recFake0001", "fields": {}}]
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.reason == "readback_row_id_mismatch"
    assert receipt.row_id == "recFake0001"


def test_sandbox_pass_a_missing_total_is_not_an_agreement(authorized, tmp_path: Path) -> None:
    """提供方没回条数 → 失败。**「核对不了」与「对得上」不是一回事。**"""
    fake = FakeBitable(total_missing=True)
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.step in {"query", "readback"}
    assert receipt.reason in {"feishu_query_total_missing", "feishu_readback_total_missing"}
    assert "条数" in receipt.detail


def test_sandbox_pass_a_provider_without_a_version_says_so(authorized, tmp_path: Path) -> None:
    """提供方不回版本号：``external_version`` 留空，回执里写明 —— **不合成**。"""
    fake = FakeBitable(version=None)
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    assert receipt.ok is True
    assert receipt.version == ""
    assert "版本号" in receipt.detail


def test_sandbox_pass_a_failed_write_never_becomes_a_notified_handoff(
    authorized, tmp_path: Path
) -> None:
    """失败的回执**不得**被用来推进交接状态。

    这是 §7 那句话的落点：「通知出去」的证据就是行 id 与版本，而失败的回执里
    没有那个证据。
    """
    fake = FakeBitable()
    fake.rows = [
        {"record_id": "recA", "fields": {BITABLE_HANDOFF_KEY_COLUMN: "handoff-0001"}},
        {"record_id": "recB", "fields": {BITABLE_HANDOFF_KEY_COLUMN: "handoff-0001"}},
    ]
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())

    with pytest.raises(ValueError, match="没有成功"):
        receipt.notified_handoff(make_handoff())


def test_sandbox_pass_a_successful_receipt_fills_the_row_id_and_version(
    authorized, tmp_path: Path
) -> None:
    """成功回执把行 id 与版本填进 ``SalesHandoff`` —— §7 的「写回读回」。"""
    fake = FakeBitable()
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff())
    assert receipt.ok

    notified = receipt.notified_handoff(make_handoff())

    assert notified.state is HandoffState.NOTIFIED
    assert notified.external_row_id == "recFake0001"
    assert notified.external_version == "17"


def test_a_handoff_that_was_never_pushed_has_no_row_id() -> None:
    """反面：本仓自己产出的交接在推送之前是 ``PENDING_LOCAL``，没有行 id。"""
    handoff = make_handoff()
    assert handoff.state is HandoffState.PENDING_LOCAL
    assert handoff.external_row_id == ""


# --------------------------------------------------------------------------
# 3b. 不截断：放不下就拒写，且**不发** HTTP
# --------------------------------------------------------------------------


def test_sandbox_pass_an_oversized_reply_body_is_refused_not_truncated(
    authorized, tmp_path: Path
) -> None:
    """正文超过上限 → 拒写，说得出超了哪一列，并且**没有截断、没有发请求**。

    静默截断是本条最容易出的错：一封被切掉结尾的回信，读的人看不出少了什么。
    """
    fake = FakeBitable()
    body = "x" * (MAX_BITABLE_TEXT_CHARS + 1)
    receipt = _client(fake, _configured(tmp_path)).push_handoff(make_handoff(reply_body=body))

    assert receipt.ok is False
    assert receipt.step == "serialisation"
    assert receipt.reason == "text_field_over_limit"
    assert BITABLE_HANDOFF_FIELD_MAP["reply_body"] in receipt.detail
    # 报的是**真实长度**，不是「超了」两个字：处置的办法取决于超了多少。
    assert str(MAX_BITABLE_TEXT_CHARS + 1) in receipt.detail
    assert fake.calls == [], "拒写发生在网络之前"


def test_the_mapping_never_truncates() -> None:
    """映射层也不得动正文 —— 它连上限都不问，因为它不改内容。"""
    body = "y" * (MAX_BITABLE_TEXT_CHARS + 10)
    record = to_bitable_handoff_record(make_handoff(reply_body=body))
    assert record[BITABLE_HANDOFF_FIELD_MAP["reply_body"]] == body
    assert len(record[BITABLE_HANDOFF_FIELD_MAP["reply_body"]]) == len(body)


# --------------------------------------------------------------------------
# 4. 独立授权：默认关、env 打不开、未授权时一次 HTTP 都不发
# --------------------------------------------------------------------------


def test_sandbox_pass_unauthorized_write_is_refused_and_sends_nothing(tmp_path: Path) -> None:
    """**未授权 → 拒写 + 零 HTTP。** 传输替身被调用就会炸，所以这条有判别力。

    这里用的是**有凭据**的 ``Settings``：拒写的理由是授权，不是凭据 ——
    两件事混在一起，会让运维以为补上凭据就好了。
    """
    transport = _ExplodingTransport()
    client = FeishuBitableClient(_configured(tmp_path), client=httpx.Client(transport=transport))

    receipt = client.push_handoff(make_handoff())

    assert transport.hits == [], "未授权的写入路径不得发出任何 HTTP 请求"
    assert receipt.ok is False
    assert receipt.step == "authorization"
    assert receipt.reason == "feishu_write_not_authorized"
    assert "授权" in receipt.detail
    assert receipt.row_id == ""


def test_the_write_path_exists_and_says_what_is_missing(tmp_path: Path) -> None:
    """§9：未授权**不得**只剩 ``NotImplementedError`` / ``interface_only`` / 空函数。

    这条路径必须存在、可调用，并说得出缺的是哪一份授权、以及真实写入现在的状态。
    """
    transport = _ExplodingTransport()
    client = FeishuBitableClient(_configured(tmp_path), client=httpx.Client(transport=transport))

    receipt = client.push_handoff(make_handoff())  # 可调用，不抛

    assert isinstance(receipt, BitableWriteReceipt)
    assert "FEISHU_WRITE_AUTHORIZED" in receipt.detail
    assert LIVE_WRITE_STATE in receipt.detail
    assert all(blocker in receipt.detail for blocker in LIVE_WRITE_BLOCKERS)


def test_the_authorization_defaults_to_closed(tmp_path: Path) -> None:
    """默认关。这条判据在有人把常量改成 True 的那一天会红。"""
    settings = _configured(tmp_path)
    assert settings.feishu_write_authorized is False
    assert FeishuBitableClient(settings).write_authorized is False


def test_sandbox_pass_authorization_cannot_be_opened_by_env_or_argument(
    monkeypatch, tmp_path: Path
) -> None:
    """**env 打不开，参数也不是。** 与 ``send_authorized`` 逐字同型的一条纪律。

    三件事一起断：环境变量无效、属性没有 setter、构造参数里没有它的位置。
    """
    import inspect

    monkeypatch.setenv("MARKETOPS_FEISHU_WRITE_AUTHORIZED", "true")
    monkeypatch.setenv("FEISHU_WRITE_AUTHORIZED", "true")

    settings = _configured(tmp_path)
    assert settings.feishu_write_authorized is False

    with pytest.raises(AttributeError):
        settings.feishu_write_authorized = True  # type: ignore[misc]

    parameters = set(inspect.signature(FeishuBitableClient.__init__).parameters)
    assert parameters == {"self", "settings", "client"}
    assert not any("authorized" in name for name in parameters)


def test_unauthorized_is_checked_before_credentials(tmp_path: Path) -> None:
    """授权先于凭据被检查，而顺序是**故意的**。

    授权是唯一一个运维补不了的缺项。把它排在凭据之后，操作员会在补完凭据与网络
    之后撞上一堵他打不开的墙 —— 而之前每一次都以为快好了。
    """
    transport = _ExplodingTransport()
    client = FeishuBitableClient(
        _configured(tmp_path, feishu_app_id=None, feishu_app_secret=None),
        client=httpx.Client(transport=transport),
    )

    receipt = client.push_handoff(make_handoff())

    assert client.configured is False
    assert receipt.step == "authorization"
    assert receipt.reason == "feishu_write_not_authorized"


# --------------------------------------------------------------------------
# 4b. 凭据不进回执：一次真的脱敏判据
# --------------------------------------------------------------------------


def test_sandbox_pass_a_provider_message_that_echoes_the_secret_is_redacted(
    authorized, tmp_path: Path
) -> None:
    """提供方的 ``msg`` 可能把请求里的东西原样回显 —— 回执里不得出现 secret。

    这不是假想：一条「凭据无效」的错误信息里，出厂就带着我们刚发过去的那一段。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url.path).endswith(TOKEN_SUFFIX):
            return httpx.Response(
                200,
                json={
                    "code": 10003,
                    "msg": f"app_secret {FAKE_SECRET} is invalid, token {FAKE_TOKEN} rejected",
                },
            )
        raise AssertionError("令牌就失败了，后面的请求根本不该发出来")

    client = FeishuBitableClient(
        _configured(tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    receipt = client.push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.step == "token"
    assert FAKE_SECRET not in receipt.detail
    assert "<redacted>" in receipt.detail
    assert FAKE_SECRET not in json.dumps(receipt.as_public_dict())


def test_sandbox_pass_a_transport_failure_is_a_receipt_not_an_exception(
    authorized, tmp_path: Path
) -> None:
    """连不上是**一次失败回执**，不是一个冒到调用栈上的异常。

    调用方拿到异常只能丢掉它；回执会被写进记录 —— 那是事后唯一能读的东西。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sandbox has no route to the vendor")

    client = FeishuBitableClient(
        _configured(tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    receipt = client.push_handoff(make_handoff())

    assert receipt.ok is False
    assert receipt.step == "token"
    assert receipt.reason == "feishu_token_failed"
    assert "no route" in receipt.detail


# --------------------------------------------------------------------------
# 5. BLOCKED 是一个取值，不是一句话
# --------------------------------------------------------------------------


def test_the_live_write_is_marked_blocked_with_all_its_blockers() -> None:
    """真实飞书写入 = BLOCKED，且缺的**三样都列出来**。

    只写「缺凭据」，会让运维补完凭据之后撞上另外两堵墙 —— 而每一次都以为快好了。

    第三样**不是**一条关于网络的断言：本机 DNS 能把厂商域名解出地址，所以
    「没有路由」是一句没量过的话。这一格钉的是那句被拿掉之后留下的实话 ——
    「从未对厂商执行过一次」，于是真实通过与失败今天不可知。
    """
    assert LIVE_WRITE_STATE == "BLOCKED"
    assert set(LIVE_WRITE_BLOCKERS) == {
        "missing_business_write_authorization",
        "missing_credentials",
        "never_exercised_against_the_vendor",
    }
    assert not any("route" in blocker or "network" in blocker for blocker in LIVE_WRITE_BLOCKERS), (
        "不得留下一条关于网络的断言：本机 DNS 能解出厂商域名，缺的不是路由"
    )


def test_the_adapter_state_names_what_is_wired_and_what_is_not() -> None:
    """接线状态逐种记录分开 —— 一个常量说不清「哪几种记录推得了」。"""
    assert ADAPTER_STATE == "partially_wired"
    assert RECORD_KIND_STATE["handoff"] == "wired"
    assert RECORD_KIND_STATE["route"] != "wired"
    assert RECORD_KIND_STATE["opportunity"] != "wired"


def test_unwired_record_kinds_refuse_with_a_reason_that_says_what_is_missing() -> None:
    """路线 / 机会的拒写理由说的是**缺一个决定**，不是缺代码。

    「没有一条可判定的业务键」是一个业务决定（同一件事推两次算不算同一件事），
    与「我们忘了写」不是一回事 —— 两者在报告里必须分得开。
    """
    from marketops.domain.entities import ContactRoute

    client = FeishuBitableClient(_configured(Path("/tmp")))  # noqa: S108 - 不落盘、不出网
    route = ContactRoute(
        # 血统是必填、无默认值的（见 ``domain/entities.py`` 的 ContactRoute.workspace）。
        # 这里取的正是 ``_configured`` 那个部署的 profile 值。
        workspace=DEFAULT_PROFILE,
        account_id="account-1",
        organisation="Example Trust",
        email="grounds@example.co.uk",
        evidence_ids=["evidence-1"],
    )

    result = client.push_routes([route])

    assert result.ok is False
    assert result.reason == "route_not_wired"
    assert "业务键" in result.detail


def test_every_receipt_step_is_one_of_the_declared_steps(authorized, tmp_path: Path) -> None:
    """回执里的 ``step`` 必须是声明过的那几个之一 —— 否则「哪一步失败」不可枚举。"""
    good = _client(FakeBitable(), _configured(tmp_path)).push_handoff(make_handoff())
    assert good.ok is True
    assert good.step in WRITE_STEPS

    bad = _client(FakeBitable(total_missing=True), _configured(tmp_path)).push_handoff(make_handoff())
    assert bad.ok is False
    assert bad.step in WRITE_STEPS


#: 带 ``sandbox_pass`` 的用例名单。留成取值而不是靠 grep 一个命名约定：
#: 「哪些用例是隔离的」这个问题于是有一个可读的答案。
SANDBOX_PASS_CASES: tuple[str, ...] = tuple(
    sorted(name for name in globals() if name.startswith("test_sandbox_pass_"))
)


def test_the_sandbox_pass_names_are_declared_and_non_empty() -> None:
    """隔离通过的那几条必须真的存在 —— 名字没了，这份声明就成了对空集成立的空话。"""
    assert len(SANDBOX_PASS_CASES) >= 8, SANDBOX_PASS_CASES
    for name in SANDBOX_PASS_CASES:
        assert callable(globals()[name]), name


def test_this_file_never_points_at_the_real_vendor() -> None:
    """这份测试里不得出现真实厂商域名 —— 隔离的判据是**读**出来的，不是承诺。

    真实写入是 BLOCKED 的（见 ``client.LIVE_WRITE_STATE``），所以一条指向真实
    主机的用例会把「没验过」伪装成「验过了」。主机名在这里拆开拼，
    否则这条判据会命中它自己。
    """
    source = Path(__file__).read_text(encoding="utf-8")
    for host in ("open." + "feishu" + ".cn", "feishu" + ".cn", "larksuite" + ".com"):
        assert not re.search(re.escape(host), source), (
            f"{host} 出现在这份测试里 —— 隔离测试不得指向真实主机。"
        )
