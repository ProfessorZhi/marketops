"""Demo 账号清单的装载与校验测试。

本文件同样**不包含任何真实账号** —— 它只验证「清单形状对不对」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.domain.enums import Cohort
from marketops.domain.errors import ConfigError
from marketops.domain.evidence import AccountRecord
from marketops.application.cohorts import (
    ACCOUNTS_FILE,
    CohortPlan,
    DEFAULT_PLAN,
    load_accounts,
    validate_plan,
)

FIXTURE = Path(__file__).parent / "fixtures" / ACCOUNTS_FILE


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "accounts.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- 计划 ---------------------------------------------------------------------


def test_default_plan_is_ten_ten_ten() -> None:
    plan = DEFAULT_PLAN
    assert plan.known_success == 10
    assert plan.accessible_no_core_email == 10
    assert plan.previously_blocked == 10
    assert plan.total == 30


def test_validate_plan_reports_shortfall() -> None:
    accounts = [
        AccountRecord(account_id=f"a{i}", organisation="X", cohort=Cohort.KNOWN_SUCCESS)
        for i in range(3)
    ]
    problems = validate_plan(accounts)
    assert any("known_success" in problem for problem in problems)
    assert any("总数" in problem for problem in problems)


def test_validate_plan_passes_on_correct_shape() -> None:
    accounts = [
        AccountRecord(account_id=f"k{i}", organisation="X", cohort=Cohort.KNOWN_SUCCESS)
        for i in range(10)
    ] + [
        AccountRecord(
            account_id=f"n{i}", organisation="X", cohort=Cohort.ACCESSIBLE_NO_CORE_EMAIL
        )
        for i in range(10)
    ] + [
        AccountRecord(account_id=f"b{i}", organisation="X", cohort=Cohort.PREVIOUSLY_BLOCKED)
        for i in range(10)
    ]
    assert validate_plan(accounts, CohortPlan()) == []


# --- 装载 ---------------------------------------------------------------------


def test_fixture_is_empty_placeholder() -> None:
    """出厂夹具不含任何账号 —— 真实清单必须由使用者提供。"""
    assert FIXTURE.is_file()
    assert load_accounts(FIXTURE) == []


def test_load_accounts_parses_yaml(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
accounts:
  - account_id: real-001
    organisation: Some Coatings Ltd
    cohort: known_success
    domain: some.co.uk
""",
    )
    accounts = load_accounts(path)
    assert len(accounts) == 1
    assert accounts[0].cohort is Cohort.KNOWN_SUCCESS
    assert accounts[0].domain == "some.co.uk"


def test_missing_file_raises_with_guidance(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="账号清单不存在"):
        load_accounts(tmp_path / "nope.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML"):
        load_accounts(_write(tmp_path, "accounts: [unclosed"))


def test_wrong_shape_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="accounts"):
        load_accounts(_write(tmp_path, "items: []"))


def test_unknown_cohort_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
accounts:
  - account_id: x
    organisation: X
    cohort: not_a_real_cohort
""",
    )
    with pytest.raises(ConfigError, match="第 1 项非法"):
        load_accounts(path)


def test_duplicate_account_id_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
accounts:
  - account_id: dup
    organisation: A
    cohort: known_success
  - account_id: dup
    organisation: B
    cohort: known_success
""",
    )
    with pytest.raises(ConfigError, match="重复"):
        load_accounts(path)


def test_path_chars_in_account_id_are_rejected(tmp_path: Path) -> None:
    """account_id 会变成文件名 —— 路径字符必须在模型层就挡住。"""
    path = _write(
        tmp_path,
        """
accounts:
  - account_id: "../escape"
    organisation: X
    cohort: known_success
""",
    )
    with pytest.raises(ConfigError):
        load_accounts(path)
