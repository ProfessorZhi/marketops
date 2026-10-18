"""共享测试夹具。

原则：**任何测试都不得依赖真实 Serper key 或飞书凭据。**
需要一个不存在的凭据来验证「缺凭据时的行为」时，也用显式构造的空 Settings，
而不是去读真实的 secret 区。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.config import REPO_ROOT, Settings
from marketops.domain.enums import Cohort
from marketops.domain.evidence import AccountRecord
from marketops.adapters.profile_loader import load_profile
from marketops.domain.profile import ProfileBundle

from .fixtures import http_transport

PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """带临时运行目录的 Settings，无任何凭据。"""
    resolved = Settings(data_root=tmp_path / "runtime", profiles_dir=PROFILES_DIR, profile=DEFAULT_PROFILE)
    resolved.ensure_data_dirs()
    return resolved


@pytest.fixture
def bundle(settings: Settings) -> ProfileBundle:
    return load_profile(settings)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def account() -> AccountRecord:
    """一个测试用账号。**不是**真实目标 —— 真实清单不在 repo 里。"""
    return AccountRecord(
        account_id="test-account-001",
        organisation="Example Coatings Ltd",
        cohort=Cohort.KNOWN_SUCCESS,
        domain="example.co.uk",
    )


@pytest.fixture
def blocked_account() -> AccountRecord:
    return AccountRecord(
        account_id="test-account-002",
        organisation="Blocked Paints Ltd",
        cohort=Cohort.PREVIOUSLY_BLOCKED,
        domain="blocked.example.co.uk",
    )


@pytest.fixture
def requests_seen() -> list[str]:
    """**真的发出过**的那几跳，按顺序，且每个用例开始时是空的。

    定义在这里而不是在某一个测试文件里，是因为它现在有两个消费者
    （``test_http_access_governance.py`` 与 ``test_http_fetch_attempts.py``），
    而两份实现会在清理时机上分叉 —— 一份清了 ``WIRE``、另一份没清，于是某一条
    判据会读到上一个用例留下的地址，而它读起来像「这一跳真的发出去了」。

    返回的是 ``tests/fixtures/http_transport`` 里那一个列表**本身**，不是副本：
    判据要读的是它，而一个副本在断言时永远是空的。
    """
    http_transport.reset()
    return http_transport.REQUESTS
