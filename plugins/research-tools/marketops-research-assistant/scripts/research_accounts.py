"""Local billing accounts and key references; secrets remain in the OS keyring.

The legacy account ref stays equal to the provider so historical usage is never
rebased. Several keys can refer to one billing account, which has one balance.
Only locally enabled Jev accounts participate in quota failover. Serper's terms
prohibit multiple accounts, so its keys must share the legacy billing account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path

PROVIDERS = ("serper", "serpapi", "tinyfish", "typesafe_jev")
KEY_NAMES = {"serper": "SERPER_API_KEY", "serpapi": "SERPAPI_API_KEY",
             "tinyfish": "TINYFISH_API_KEY", "typesafe_jev": "TYPESAFE_API_KEY"}
FAILOVER_PROVIDERS = frozenset({"typesafe_jev"})
KEY_ALIAS_PROVIDERS = frozenset({"serper", "typesafe_jev"})
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def path(root):
    return Path(root) / "state" / "provider-accounts.sqlite3"


@contextmanager
def transaction(root):
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target, timeout=30)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS accounts (provider TEXT, account_ref TEXT, "
                   "owner TEXT, priority INTEGER, enabled INTEGER, failover INTEGER, "
                   "PRIMARY KEY(provider,account_ref))")
        db.execute("CREATE TABLE IF NOT EXISTS keys (provider TEXT, key_id TEXT, "
                   "account_ref TEXT, slot TEXT, fingerprint TEXT, enabled INTEGER, "
                   "PRIMARY KEY(provider,key_id))")
        db.execute("CREATE TABLE IF NOT EXISTS balances (account_ref TEXT PRIMARY KEY, "
                   "reading TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS prompts (account_ref TEXT PRIMARY KEY, "
                   "state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS intents (run_id TEXT, provider TEXT, "
                   "fingerprint TEXT, answer TEXT, PRIMARY KEY(run_id,provider,fingerprint))")
        yield db


def query(root, sql, parameters=()):
    if not path(root).is_file():
        return []
    with closing(sqlite3.connect(path(root).as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='accounts'").fetchone() is None:
            return []
        return db.execute(sql, parameters).fetchall()


def _provider(provider):
    if provider not in PROVIDERS:
        raise ValueError("ACCOUNT_PROVIDER_INVALID")


def register(root, *, provider, owner, account_id="primary", priority=0,
             enabled=True, allow_failover=False, confirm=False):
    _provider(provider)
    if confirm is not True:
        raise ValueError("ACCOUNT_LOCAL_CONFIRMATION_REQUIRED")
    if (not isinstance(owner, str) or not 3 <= len(owner) <= 254
            or any(c in owner for c in "\r\n\x00") or "@" not in owner):
        raise ValueError("ACCOUNT_OWNER_INVALID")
    if not isinstance(account_id, str) or not _ID.fullmatch(account_id):
        raise ValueError("ACCOUNT_ID_INVALID")
    if type(priority) is not int or not 0 <= priority <= 10000:
        raise ValueError("ACCOUNT_PRIORITY_INVALID")
    if type(enabled) is not bool or type(allow_failover) is not bool:
        raise ValueError("ACCOUNT_OPTIONS_INVALID")
    if provider == "serper" and account_id != "primary":
        raise ValueError("SERPER_SINGLE_ACCOUNT_ONLY")
    if account_id != "primary" and provider not in FAILOVER_PROVIDERS:
        raise ValueError("ACCOUNT_POOL_NOT_SUPPORTED")
    if allow_failover and provider not in FAILOVER_PROVIDERS:
        raise ValueError("ACCOUNT_FAILOVER_NOT_SUPPORTED")
    ref = provider if account_id == "primary" else provider + "--" + account_id
    with transaction(root) as db:
        old = db.execute("SELECT owner FROM accounts WHERE provider=? AND account_ref=?",
                         (provider, ref)).fetchone()
        if old and old[0].casefold() != owner.casefold():
            raise ValueError("ACCOUNT_OWNER_CHANGE_REQUIRES_NEW_ACCOUNT")
        db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?) ON CONFLICT(provider,account_ref) "
                   "DO UPDATE SET priority=excluded.priority,enabled=excluded.enabled,"
                   "failover=excluded.failover", (provider, ref, owner, priority, enabled, allow_failover))
        if account_id == "primary":
            db.execute("INSERT OR IGNORE INTO keys VALUES (?,?,?,?,?,?)",
                       (provider, "default", ref, "legacy", "", 1))
    return {"status": "ACCOUNT_REGISTERED", "provider": provider, "account_ref": ref,
            "keys_unchanged": True, "network_calls": 0}


def entries(root, provider):
    _provider(provider)
    rows = query(root, "SELECT account_ref,priority,enabled,failover FROM accounts "
                 "WHERE provider=? ORDER BY priority,account_ref", (provider,))
    if not rows:
        return [{"account_ref": provider, "priority": 0, "enabled": True, "failover": False}]
    return [dict(zip(("account_ref", "priority", "enabled", "failover"), row)) for row in rows]


def credential(root, provider, account_ref):
    """Private credential lookup. Never call from an MCP status/metadata tool."""
    _provider(provider)
    from research_credentials import _backend, _present, KEYRING_SERVICE
    slots = query(root, "SELECT slot FROM keys WHERE provider=? AND account_ref=? AND enabled=1 "
                  "ORDER BY CASE WHEN key_id='default' THEN 0 ELSE 1 END,key_id",
                  (provider, account_ref))
    if not slots and account_ref == provider:
        slots = [("legacy",)]
    backend = None
    for (slot,) in slots:
        if slot == "legacy":
            value = os.environ.get(KEY_NAMES[provider])
            if _present(value):
                return value
        if backend is None:
            backend = _backend()
        value = backend.get_password(KEYRING_SERVICE,
                                     KEY_NAMES[provider] if slot == "legacy" else slot)
        if _present(value):
            return value
    return None


def configure_key(root, *, provider, account_ref, key_id, value, confirm=False):
    _provider(provider)
    if provider not in KEY_ALIAS_PROVIDERS:
        raise ValueError("ACCOUNT_KEY_ALIAS_NOT_SUPPORTED")
    from research_credentials import _backend, _present, KEYRING_SERVICE
    if (confirm is not True or not isinstance(key_id, str) or not _ID.fullmatch(key_id)
            or not isinstance(value, str) or not _present(value)
            or any(c in value for c in "\r\n\x00")):
        raise ValueError("ACCOUNT_KEY_INPUT_INVALID")
    fingerprint = hashlib.sha256(value.encode()).hexdigest()
    # A second alias for the same key must not manufacture an independent wallet.
    if account_ref != provider:
        legacy = credential(root, provider, provider)
        if legacy and hashlib.sha256(legacy.encode()).hexdigest() == fingerprint:
            raise ValueError("KEY_ALREADY_BELONGS_TO_ANOTHER_ACCOUNT")
    slot = "account/" + provider + "/" + account_ref + "/" + key_id
    backend = _backend()
    with transaction(root) as db:
        if not db.execute("SELECT 1 FROM accounts WHERE provider=? AND account_ref=?",
                          (provider, account_ref)).fetchone():
            raise ValueError("ACCOUNT_NOT_REGISTERED")
        other = db.execute("SELECT account_ref FROM keys WHERE provider=? AND fingerprint=?",
                           (provider, fingerprint)).fetchone()
        bound = db.execute("SELECT account_ref FROM keys WHERE provider=? AND key_id=?",
                           (provider, key_id)).fetchone()
        if (other and other[0] != account_ref) or (bound and bound[0] != account_ref):
            raise ValueError("KEY_ALREADY_BELONGS_TO_ANOTHER_ACCOUNT")
        try:
            backend.set_password(KEYRING_SERVICE, slot, value)
        except Exception:
            raise ValueError("OS_KEYRING_WRITE_FAILED") from None
        db.execute("INSERT INTO keys VALUES (?,?,?,?,?,1) ON CONFLICT(provider,key_id) "
                   "DO UPDATE SET slot=excluded.slot,fingerprint=excluded.fingerprint,enabled=1",
                   (provider, key_id, account_ref, slot, fingerprint))
    return {"status": "ACCOUNT_KEY_SAVED", "account_ref": account_ref, "key_id": key_id,
            "secret_values_returned": False, "network_calls": 0}


def claim_balance_question(root, account_ref):
    import research_balances
    if research_balances.readings(root).get(account_ref, {}).get("left") is not None:
        return {"account_ref": account_ref, "ask_now": False}
    with transaction(root) as db:
        changed = db.execute("INSERT OR IGNORE INTO prompts VALUES (?, 'ASKED')",
                             (account_ref,)).rowcount
    return {"account_ref": account_ref, "ask_now": bool(changed)}


def activate_key(root, *, provider, key_id, confirm=False):
    _provider(provider)
    if confirm is not True:
        raise ValueError("ACCOUNT_LOCAL_CONFIRMATION_REQUIRED")
    with transaction(root) as db:
        row = db.execute("SELECT account_ref FROM keys WHERE provider=? AND key_id=?",
                         (provider, key_id)).fetchone()
        if row is None:
            raise ValueError("ACCOUNT_KEY_NOT_REGISTERED")
        db.execute("UPDATE keys SET enabled=0 WHERE provider=? AND account_ref=?", (provider, row[0]))
        db.execute("UPDATE keys SET enabled=1 WHERE provider=? AND key_id=?", (provider, key_id))
    return {"status": "ACCOUNT_KEY_ACTIVATED", "account_ref": row[0], "key_id": key_id,
            "balance_and_usage_unchanged": True}


def status(root):
    """Metadata only: even a missing keyring must not expose/read secrets here."""
    import research_balances
    readings = research_balances.readings(root)
    prompts = dict(query(root, "SELECT account_ref,state FROM prompts"))
    accounts = []
    for provider in PROVIDERS:
        for entry in entries(root, provider):
            ref = entry["account_ref"]
            keys = query(root, "SELECT key_id,enabled FROM keys WHERE provider=? AND account_ref=? "
                          "ORDER BY key_id", (provider, ref))
            reading = readings.get(ref)
            reported = bool(reading and reading.get("left") is not None)
            accounts.append({"provider": provider, **entry, "key_ids": [k[0] for k in keys],
                             "active_key_ids": [k[0] for k in keys if k[1]],
                             "balance_state": "REPORTED" if reported else prompts.get(ref, "UNREPORTED"),
                             "ask_balance_once": provider in {"serper", "typesafe_jev"}
                             and not reported and ref not in prompts})
    return {"accounts": accounts, "failover_providers": sorted(FAILOVER_PROVIDERS),
            "secret_values_returned": False, "mutates_nothing": True}


def claim_intent(root, run_id, provider, fingerprint):
    with transaction(root) as db:
        old = db.execute("SELECT answer FROM intents WHERE run_id=? AND provider=? AND fingerprint=?",
                         (run_id, provider, fingerprint)).fetchone()
        if old is not None:
            return False, json.loads(old[0]) if old[0] else None
        db.execute("INSERT INTO intents VALUES (?,?,?,NULL)", (run_id, provider, fingerprint))
    return True, None


def finish_intent(root, run_id, provider, fingerprint, answer):
    with transaction(root) as db:
        if answer.get("status") == "BLOCKED" and not answer.get("ledger_operation_id"):
            db.execute("DELETE FROM intents WHERE run_id=? AND provider=? AND fingerprint=?",
                       (run_id, provider, fingerprint))
            return
        db.execute("UPDATE intents SET answer=? WHERE run_id=? AND provider=? AND fingerprint=?",
                   (json.dumps(answer), run_id, provider, fingerprint))


def historical_intent(root, run_id, provider, fingerprint):
    """Enabling a pool cannot buy a legacy single-account operation again."""
    import research_usage_ledger as usage
    from types import SimpleNamespace
    if not usage.default_path(root).is_file():
        return None
    project = usage.project_for_root(root)
    ledger_fingerprint = usage.intent_fingerprint(
        SimpleNamespace(project=project, run_id=run_id, intent=fingerprint))
    return next((op for op in usage.for_root(root).operations(
        project=project, run_id=run_id)
        if op.provider == provider and op.intent_fingerprint == ledger_fingerprint), None)


def select(root, provider):
    """Choose locally authorised accounts; never rotate on a provider cooldown."""
    import research_provider_health as health
    import research_costs
    available = [a for a in entries(root, provider) if a["enabled"] and
                 (a["account_ref"] == provider or
                  (provider in FAILOVER_PROVIDERS and a["failover"]))]
    if not available:
        raise ValueError("NO_ENABLED_PROVIDER_ACCOUNT")
    pooled = any(a["account_ref"] != provider for a in available)
    supplier = health.blocked(root, provider)
    if supplier and supplier["state"] == "RATE_LIMITED":
        return available[0], supplier, pooled
    registered = query(root, "SELECT 1 FROM accounts WHERE provider=?", (provider,))
    readings = research_costs.balances(root, read=False) if pooled or registered else {}
    last = None
    for account in available:
        ref = account["account_ref"]
        known = health.blocked(root, ref)
        remaining = readings.get(ref, {}).get("estimated_left_now")
        if known is None and remaining is not None and float(remaining) <= 0:
            known = health.record(root, provider, "QUOTA_EXHAUSTED", account_ref=ref,
                                  detail="reported balance minus local usage is exhausted")
        if known is None or known["state"] != "QUOTA_EXHAUSTED":
            return account, known, pooled
        last = (account, known, pooled)
    return last


def has_local_key(root, provider):
    if provider not in KEY_ALIAS_PROVIDERS:
        return False
    refs = {a["account_ref"] for a in entries(root, provider) if a["enabled"] and
            (a["account_ref"] == provider or a["failover"])}
    return any(ref in refs for (ref,) in query(root,
        "SELECT account_ref FROM keys WHERE provider=? AND enabled=1 AND slot<>'legacy'", (provider,)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "register", "configure-key", "activate-key"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = status(args.data_root)
        else:
            if not args.stdin:
                raise ValueError("ACCOUNT_INPUT_REQUIRES_STDIN")
            values = json.loads(sys.stdin.read())
            function = {"register": register, "configure-key": configure_key,
                        "activate-key": activate_key}[args.action]
            result = function(args.data_root, **values, confirm=args.confirm)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError else "ACCOUNT_SETUP_FAILED"
        if not re.fullmatch(r"[A-Z_]{3,80}", code):
            code = "ACCOUNT_SETUP_FAILED"
        print(json.dumps({"status": "BLOCKED", "code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
