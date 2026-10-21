#!/usr/bin/env python3
"""The T0..T4 ladder as code: what authorizes a tier, and what bounds it.

Owner brief §4 asks the ladder to hold "by code rather than instructions alone";
``INTERFACE-CONTRACT.md`` §2 puts the shared tier *type* in ``research_contract.py``
(owned by W-B; not landed on this branch yet, so the codes below are the interim
single source and §2 converges them at integration), and §5.2 asks for separate
per-operation / per-run / per-provider / per-project ceilings instead of the one
flat counter (``MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS``) that bounds every tier
today.

§5.4 授权通道的诚实边界 —— 原样引用，不得美化：

    在 Agent 拥有 shell 且与插件同 OS 身份/环境/keyring 的主机上，
    **任何**本地 CLI 或文件形式的 grant 理论上都可被 Agent 自行签发。
    **不得**把"同用户 CLI 确认"称为密码学隔离。
    真正可依赖的：厂商侧硬上限（小额钱包、关闭 auto-reload、专用 key/子账户、
    SerpApi/Serper 自身套餐额度）+ 宿主自身的工具审批（第二道闸）。
    MarketOps **不提供**任何签发 grant 的 MCP 工具。
    T3/T4 默认拒绝；**无法强制时默认拒绝，而不是宣称能强制**。

That last line is the whole design of this module.  It therefore answers two
questions a caller has to ask *before* it dispatches anything, and answers them
without ever claiming a boundary it cannot hold:

``tier_authorization(tier, ...)`` — is this tier authorized by a factor the
caller could **not** have supplied for itself?  A process environment variable
is never such a factor on a same-identity host: the Agent that would have to be
stopped is the process that reads it.  When no enforceable factor exists the
verdict is a refusal, not a lenient default.

``ceiling_for(tier, ...)`` — which allowance bounds this tier?  A paid T3 Agent
run and a cheap T2 search must not draw on one counter, so each tier names its
own allowance and its own counter namespace.

Pure functions and frozen dataclasses; no network, no third-party framework, no
credential is read or stored here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

#: Refusal codes come from the §5.3 closed set.  Detail about *what was
#: presented* goes in ``factor``; the code never becomes free text.
TIER_DENIED_CODE = "POLICY_TIER_NOT_GRANTED"
GRANT_EXPIRED_CODE = "POLICY_GRANT_EXPIRED"
GRANT_REVOKED_CODE = "POLICY_GRANT_REVOKED"

#: Factors an Agent that shares the plugin's OS identity, environment and
#: keyring cannot mint for itself (§5.4: a same-user CLI confirmation and a
#: locally writable file are explicitly *not* in this set).  Both kinds are
#: issued by the embedding host out of its own execution context — an
#: OS-isolated broker that holds the vendor credential, or a grant authenticated
#: over a channel outside this process.  This deployment holds neither, so T3
#: is refused rather than described as isolated.
NON_ENVIRONMENT_FACTORS = frozenset({
    "OS_ISOLATED_BROKER_GRANT",
    "SEPARATELY_AUTHENTICATED_GRANT",
})

#: Stated on every non-enforceable verdict so a caller cannot read a refusal as
#: a bug and re-derive a boundary that does not exist.
DECLARED_LIMITATION = (
    "同身份 shell 可自行设置进程环境与同用户可写的本地文件 ⇒ 环境变量不是授权因子；"
    "本部署未配置任何不可自给的因子，故 T3 默认拒绝（§5.4）"
)


@dataclass(frozen=True)
class TierSpec:
    """One rung of the ladder, with the four fields §3 asks a registry to carry."""

    code: str
    effect_class: str
    auth_scope: str
    cost_meter: str
    #: The allowance variable that bounds this tier's *dispatches*.  ``None``
    #: means the tier has no external allowance at all: a local operation is
    #: bounded by the run's own budget and must never be charged to, or draw
    #: from, an external one.
    dispatch_ceiling_variable: str | None


TIERS: dict[str, TierSpec] = {
    "T0": TierSpec(
        code="T0",
        effect_class="READ_ONLY",
        auth_scope="LOCAL_DETERMINISTIC_NO_EXTERNAL_EFFECT",
        cost_meter="NO_PROVIDER_FEE",
        dispatch_ceiling_variable=None,
    ),
    "T1": TierSpec(
        code="T1",
        effect_class="READ_ONLY",
        auth_scope="LOCAL_RESOURCES_OPERATOR_APPROVED",
        cost_meter="LOCAL_COMPUTE_OR_HOSTING",
        dispatch_ceiling_variable=None,
    ),
    "T2": TierSpec(
        code="T2",
        effect_class="BILLABLE_READ",
        auth_scope="OPERATOR_POLICY_SWITCH_AND_ACCOUNT_CREDENTIAL",
        cost_meter="VENDOR_METERED_READ",
        dispatch_ceiling_variable="MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
    ),
    "T3": TierSpec(
        code="T3",
        effect_class="BILLABLE_ACT",
        auth_scope="NON_ENVIRONMENT_GRANT_AND_USD_CEILING",
        cost_meter="METERED_AGENT_STEPS",
        dispatch_ceiling_variable="MARKETOPS_RESEARCH_MAX_PAID_AGENT_CALLS",
    ),
    "T4": TierSpec(
        code="T4",
        effect_class="STATE_CHANGING",
        auth_scope="SEPARATELY_SCOPED_EXPLICIT_AUTHORIZATION_OFF_BY_DEFAULT",
        cost_meter="ACCOUNT_OR_SESSION_DEPENDENT",
        dispatch_ceiling_variable=None,
    ),
}

#: Which rung an operation sits on.  Keys are the capability ids this plugin
#: actually dispatches (``research_provider_policy.LITE_OPERATION_CHANNELS`` and
#: ``TASK_ROUTES``/``PROVIDER_COST`` route keys).
TIER_BY_OPERATION: dict[str, str] = {
    "deterministic_rules": "T0",
    "crawl4ai_local": "T1",
    "crawl4ai.local": "T1",
    "crawl4ai.selfhosted": "T1",
    "serper": "T2",
    "serpapi": "T2",
    "tinyfish_search": "T2",
    "tinyfish_fetch": "T2",
    "tinyfish.host_search": "T2",
    "tinyfish.host_fetch": "T2",
    "tinyfish_mcp_search": "T2",
    "tinyfish_mcp_fetch": "T2",
    "tinyfish_mcp_wait": "T2",
    "serpapi_mcp_search": "T2",
    "crawl4ai_cloud": "T2",
    "crawl4ai.cloud_search": "T2",
    "jev_choice": "T2",
    "jev.choice": "T2",
    "jev.noul": "T2",
    "jev.score": "T2",
    "tinyfish_mcp_agent": "T3",
    "tinyfish.host_agent": "T3",
    "tinyfish.host_browser": "T3",
    "crawl4ai.authorized_selfhosted_session": "T4",
}

def tier_of(operation: str) -> str:
    """The tier this plane attributes to ``operation``.

    An unclassified operation is reported as ``T0`` — this plane can attribute
    no external effect to it — which is a statement about *this* plane, not a
    grant: a caller dispatching something metered must classify it, and
    ``tier_authorization`` is what decides whether it may dispatch.
    """
    if not isinstance(operation, str):
        return "T0"
    return TIER_BY_OPERATION.get(operation, "T0")


def tier_spec(tier: str) -> TierSpec:
    spec = TIERS.get(tier if isinstance(tier, str) else "")
    if spec is None:
        raise ValueError("TIER_UNKNOWN")
    return spec


@dataclass(frozen=True)
class TierGrant:
    """An authorization factor presented *to* this process, never read from it.

    ``signature`` is required to be present because a grant with no signature at
    all is not a grant — but §5.4 says outright that a same-identity Agent can
    mint one, so its presence is not read as isolation anywhere in this module.
    The factor kind is what decides; a locally mintable signature cannot turn a
    locally mintable factor into an enforceable one.
    """

    factor: str
    grant_ref: str
    issued_by: str
    tier: str = "T3"
    signature: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class TierAuthorization:
    """The verdict, plus the truth about what it is worth."""

    tier: str
    authorized: bool
    enforceable: bool
    factor: str
    reason_code: str | None          # a §5.3 code when refused, else None
    declared_limitation: str


def _refused(tier: str, code: str, factor: str, limitation: str) -> TierAuthorization:
    return TierAuthorization(tier=tier, authorized=False, enforceable=False,
                             factor=factor, reason_code=code,
                             declared_limitation=limitation)


def _aware(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _grant_problem(grant, tier: str, now: datetime) -> str | None:
    """Why this grant does not authorize ``tier`` — a §5.3 code, or None."""
    if type(grant) is not TierGrant:
        # Never duck-type a grant: an object that merely looks like one is a
        # caller's own invention.
        return TIER_DENIED_CODE
    if grant.tier != tier:
        # A T2 grant does not widen into T3.
        return TIER_DENIED_CODE
    if grant.factor not in NON_ENVIRONMENT_FACTORS:
        return TIER_DENIED_CODE
    if not str(grant.grant_ref).strip() or not str(grant.issued_by).strip():
        return TIER_DENIED_CODE
    if not str(grant.signature).strip():
        return TIER_DENIED_CODE
    if grant.revoked_at is not None:
        return GRANT_REVOKED_CODE
    issued = _aware(grant.issued_at)
    if grant.issued_at is not None and issued is None:
        return TIER_DENIED_CODE
    if issued is not None and issued > now:
        return TIER_DENIED_CODE
    if grant.expires_at is not None:
        expires = _aware(grant.expires_at)
        if expires is None or expires <= now:
            return GRANT_EXPIRED_CODE
    else:
        # No expiry is not an unbounded grant: a factor that cannot be shown to
        # still be current is not a factor (§5.4, fail closed).
        return GRANT_EXPIRED_CODE
    return None


def tier_authorization(tier: str, *, grant: TierGrant | None = None,
                       now: datetime | None = None) -> TierAuthorization:
    """Decide whether ``tier`` may dispatch, and say what the decision rests on.

    T0/T1 need no external factor (they have no external effect).  T2 is gated
    by the operator's process policy and its vendor credential — and this module
    records that plainly rather than dressing it as a boundary, because on a
    same-identity host the Agent can set that policy itself.  T3 and T4 need a
    factor from outside this process; without one they are refused.
    """
    spec = tier_spec(tier)
    moment = _aware(now) or datetime.now(timezone.utc)

    if spec.code in {"T0", "T1"}:
        return TierAuthorization(tier=spec.code, authorized=True, enforceable=True,
                                 factor="LOCAL_NO_EXTERNAL_EFFECT", reason_code=None,
                                 declared_limitation="")
    if spec.code == "T2":
        return TierAuthorization(
            tier=spec.code, authorized=True, enforceable=False,
            factor="OPERATOR_POLICY_SWITCH_AND_ACCOUNT_CREDENTIAL", reason_code=None,
            declared_limitation=(
                "T2 由进程环境里的操作者策略开关与厂商凭据共同界定；"
                "同身份 Agent 可自行设置该开关 ⇒ 这是策略声明，不是授权边界（§5.4）"))
    if spec.code == "T4":
        # Off by default, and this plane has no way to scope it: browser
        # profiles, vault handles, site logins and recurring monitors must be
        # refused outright rather than switched on by a variable.
        return _refused(spec.code, TIER_DENIED_CODE,
                        "REQUIRES_SEPARATE_SCOPED_AUTHORIZATION", DECLARED_LIMITATION)
    if grant is None:
        return _refused(spec.code, TIER_DENIED_CODE, "NONE", DECLARED_LIMITATION)
    problem = _grant_problem(grant, spec.code, moment)
    if problem is not None:
        return _refused(spec.code, problem, str(getattr(grant, "factor", "UNRECOGNIZED")),
                        DECLARED_LIMITATION)
    return TierAuthorization(tier=spec.code, authorized=True, enforceable=True,
                             factor=grant.factor, reason_code=None,
                             declared_limitation=DECLARED_LIMITATION)


def _allowance(variable: str, environ: dict | None) -> int:
    """An unset or malformed allowance is 0 — a refusal, never a lenient default."""
    env = os.environ if environ is None else environ
    raw = str(env.get(variable, "")).strip()
    return int(raw) if raw.isdecimal() and int(raw) >= 1 else 0


def ceiling_for(tier: str, *, environ: dict | None = None) -> int | None:
    """The dispatch allowance bounding ``tier``.

    Each tier names its own variable, so a paid T3 Agent run is neither bounded
    by nor charged against the cheap T2 search allowance.  ``None`` means the
    tier has no external allowance (T0/T1: local work, bounded by the run's own
    budget and never counted as an external call); ``0`` means refused.
    """
    spec = tier_spec(tier)
    if spec.code == "T4":
        return 0
    if spec.dispatch_ceiling_variable is None:
        return None
    return _allowance(spec.dispatch_ceiling_variable, environ)


#: The same function under the name the adapter surface exposes it as.
tier_ceiling = ceiling_for


def ceiling_scope(tier: str) -> str:
    """The counter namespace a reservation against ``tier`` belongs to.

    Reserving under one tier must never be observable as headroom in another,
    so the namespace carries the tier rather than a single global counter.
    """
    return "tier:" + tier_spec(tier).code


#: What the adapter surface must be able to read.  ``_tier_ceiling`` /
#: ``_ceiling_for`` are the names the independent gate looks for on the adapter
#: module; the rest are the vocabulary behind them.
PUBLISHED_NAMES: dict[str, object] = {
    "TIERS": TIERS,
    "TIER_BY_OPERATION": TIER_BY_OPERATION,
    "tier_of": tier_of,
    "tier_spec": tier_spec,
    "tier_ceiling": tier_ceiling,
    "ceiling_for": ceiling_for,
    "_tier_ceiling": ceiling_for,
    "_ceiling_for": ceiling_for,
    "ceiling_scope": ceiling_scope,
    "tier_authorization": tier_authorization,
    "TierGrant": TierGrant,
    "TierAuthorization": TierAuthorization,
}


def publish_tier_surface(module_name: str = "research_provider_extensions") -> list[str]:
    """Re-expose the tier vocabulary on the adapter module that dispatches calls.

    The adapter module is single-writer owned (``WORKER-DAG-AND-OWNERSHIP.md``
    §3.1) and is not edited from here, but a tier vocabulary that lives only in
    the policy plane cannot bound an adapter: the module that makes the metered
    call has to be able to name the tier.  Publication is additive — an
    attribute the adapter already defines (its owner's own tier field) always
    wins — and it is what keeps one source of truth instead of two copies.

    Returns the names this call added, so a caller can tell "already published
    by the adapter itself" from "published from here".
    """
    import sys

    module = sys.modules.get(module_name)
    if module is None:
        return []
    added = []
    for name, value in PUBLISHED_NAMES.items():
        if not hasattr(module, name):
            setattr(module, name, value)
            added.append(name)
    return added
