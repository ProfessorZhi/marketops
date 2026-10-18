# Client projections

One canonical Skill, three client projections. The Skill text is the same
everywhere; **a projection differs only in installation, manifest, display, the
optional session bridge and notification ability** — never in business content.

## The rule this directory exists to enforce

> A client is written as supported for a contract **only after that client has
> really run that contract**.

"Theoretically MCP-compatible" is not a delivery. So each client directory
carries a `status.json` in which **every field is a separate claim with its own
evidence**, and a claim that was not exercised is `not_verified`, `unsupported`
or `blocked_external` — never a blank that reads like a pass.

### The fields, and what each one requires

| Field | `verified` requires |
|---|---|
| `installed_projection` | the client reads this package's Skill files from the installed location |
| `mcp_connected` | a session reached the server and listed its tools |
| `auth_verified` | the client's own auth path succeeded, **and** a negative control was refused |
| `tool_catalog_verified` | the list the client received was compared against the deployment's catalog, not just counted |
| `read_flow_verified` | the client really invoked a read use case and showed its result |
| `write_flow_verified` | the client really invoked a write use case, through admission, with its permission and budget outcome observed |
| `durable_job_disconnect_verified` | the client was disconnected while a durable job ran, and the job was read afterwards |
| `reconnect_verified` | the client reconnected and read the same session's state back |
| `multi_session_isolation_verified` | two sessions were run and shown not to see each other's scope |
| `notification_pull_verified` | the client really pulled a notification event and its delivery receipts |
| `notification_push_verified` | the client was really pushed to, and the delivery attempt is visible at the server |
| `plugin_install_verified` | the client's own plugin-install path installed this package and the installed copy was compared to the source |

`notification_push_verified` and `plugin_install_verified` are optional in the
sense that a client may legitimately not have them. They are **not** optional to
report: absent means `unsupported`, an unexplained empty cell does not.

### States

| State | Means |
|---|---|
| `verified` | ran here, and `evidence` names the receipt |
| `not_verified` | possible, not yet run here; `reason` says what is missing |
| `unsupported` | this client structurally cannot do it; `reason` says why |
| `blocked_external` | the client or its environment cannot be reached from this slice; `unblock` says precisely what would lift it |

## Honest degradation, in this order

A client that cannot do the whole contract is still usable, and the ladder is
fixed: **read → write → durable job → notify**. A projection that stops partway
says where it stopped and what the user therefore has to do in a client that
went further. It never presents a lower rung as the rung above it — in
particular, an unverified notification path is never described as working, and a
client that cannot receive notifications is never described as not needing them.
