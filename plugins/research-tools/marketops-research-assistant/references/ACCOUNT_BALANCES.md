# Billing accounts, keys and balance estimates

Balances belong to a billing account, not an API key. Different providers remain
separate even when the login email is identical. Existing accounts keep refs
`serper`, `serpapi`, `tinyfish`, `typesafe_jev`; historical usage stays attached.
Metadata lives in the customer's data root. Key values stay in the OS keyring.
No owner email or key value is exposed by `marketops_accounts`.

## Ask once, then calculate

1. Read `marketops_accounts()` and `marketops_costs(action='balances')`.
2. For an account with `ask_balance_once=true`, call
   `marketops_accounts(action='ask_balance', account_ref='<ref>')`. Ask only if
   `ask_now=true`; this atomic claim prevents several windows asking together.
3. Record the console's current available amount with
   `marketops_costs(action='set_balance', provider='typesafe_jev',
   account_ref='typesafe_jev', left=5, unit='USD')`. For Serper use `unit='credit'`.
   If the user does not know, leave UNKNOWN and continue recording usage; do not
   repeat the question in later conversations. The user can report a new reading
   at any time, including after a top-up, without another setup cycle.

SerpApi and TinyFish retain their existing free balance reads. Serper and Jev
use operator reports because no documented public balance endpoint is integrated.
A reading has an observation time and completed-usage baseline. Estimate remaining
from the matching account's later ledger usage, while retaining unresolved older
exposure. Never subtract a last-30-days dashboard total from a current balance.
Refreshing a reading neither releases unknown charges nor resets task budgets.
Display reported amount, estimated amount, units, time and unknown exposure separately.

Calls and credit quantities can be exact where the meter is known. USD conversion
can remain an estimate; Jev's default request approximation is not token-exact.
Unconvertible units/rates produce UNKNOWN, not a zero charge. Other programs using
the account, credit expiry and provider billing delays cause drift. A new console
reading recalibrates estimates; it does not rewrite historical spend. TypeSafe's
public terms describe input consumption, but do not specify a balance-update delay.

## Local registration

Use the installed runtime Python and `scripts/research_accounts.py`. All mutations
require explicit `--confirm`; JSON arrives on stdin, not in command-line arguments.
Example JSON documents below are synthetic. Use a hidden local input prompt for
secrets; never paste a real key into chat, shell history, source or logs.

Register each existing provider's primary account with this stdin document:

```json
{"provider":"typesafe_jev","owner":"operator@example.org"}
```

```text
python scripts/research_accounts.py register --data-root CUSTOMER_DATA_ROOT --stdin --confirm
python scripts/research_accounts.py status --data-root CUSTOMER_DATA_ROOT
```

Primary registration only references the existing credential. It does not read,
replace or move it. Owner changes require a separate account identity. Serper/Jev
support additional key aliases; SerpApi/TinyFish retain their existing credential
configuration. To save a key alias, provide a JSON document containing `provider`,
`account_ref`, unique `key_id` and `value` to the local `configure-key` action.
Then explicitly choose it with `activate-key`, whose stdin document contains
`provider` and `key_id`. Activation changes the credential used for that account,
never its balance, spend or caps. Key aliases are not separate wallets.

## Authorised Jev account pools

Only independently authorised Jev billing accounts can opt in to quota failover.
Register a non-primary account using an `account_id`, `priority` (lower first),
`enabled=true` and `allow_failover=true`, then securely configure its key alias.
Its ref is `typesafe_jev--<account_id>` and it needs its own balance observation.
Accounts lacking explicit opt-in never participate. Disable one by repeating
registration with its original owner and `enabled=false`.

If the selected account is exhausted, subsequent new requests select the next
enabled account. A sent request is never automatically replayed on another wallet.
An identical request in the same Run cannot be billed again merely by changing
operation ID or account. Legacy requests predating pool activation are refused
with `HISTORICAL_INTENT_REQUIRES_RECONCILIATION` instead of rebought. Timeouts and
UNKNOWN charges retain their holds. A 429 starts a supplier cooldown across all
accounts; 401/403 do not trigger quota rotation. Project, Run, provider and operation
caps still apply across accounts. Additional keys provide no additional spending
authority. Provider selection does not create accounts or buy credits.

[Serper terms](https://serper.dev/terms) prohibit multiple accounts; Serper therefore
supports one billing account with key aliases. [TypeSafe MCA §8.2(b)](https://typesafe.ai/legal/mca)
expressly prohibits creating multiple accounts to obtain extra promotional credits
or avoid restrictions. Do not register accounts for that purpose; this pool is for
authorised existing billing accounts and does not bypass service limits.
