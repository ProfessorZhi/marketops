# TinyFish patches for W-INTEGRATOR — apply as ONE set

These three patches are **lockstep**: applying any subset changes behaviour in a
way the others do not expect. Measured, not assumed — see
`references/INTEGRATOR-HANDOFF-TINYFISH.md`.

| file | target | what |
|---|---|---|
| `tinyfish-official-bridge-get-run.patch` | `scripts/research_official_mcp.py` | `wait_for_run(run_id)` → `get_run(id)`; drift record → `REPAIRED_IN_THIS_PATCH` |
| `tinyfish-bridge-fetch-flags.patch` | `scripts/research_official_mcp.py` | the published `fetch_content` flags stop being "invalid arguments" |
| `tinyfish-shipping-caller.patch` | `scripts/research_provider_extensions.py` | the shipping call sites speak `get_run(id)` and send the documented fetch defaults |

Apply in that order. Each was `git apply --check`ed against the blob at
integration `cf8ff6e`; patches 2 and 3 also apply on top of 1.

**Why lockstep — measured on the integration tree:**

- patch 1 **alone** breaks the shipped `marketops_tinyfish_agent_wait` tool: the
  caller still calls `_validate("tinyfish", "wait_for_run", {"run_id": …})`, which
  patch 1 no longer allowlists → `OFFICIAL_MCP_TOOL_NOT_ALLOWLISTED` on every call.
- patch 3 **alone** breaks the shipped fetch: the bridge's argument allowlist
  rejects `links`/`image_links`/`page_metadata` → `TINYFISH_MCP_FETCH_ARGS_INVALID`.
  That is what patch 2 is for.

With 1+2+3 the four shipping shapes all validate:

```
search   search             ACCEPTED
fetch    fetch_content      ACCEPTED
wait     get_run            ACCEPTED
agent    run_web_automation ACCEPTED   (still with agent_config — see the gap below)
```

**Still not fixed by this set:** the paid Agent path keeps requiring
`agent_config.{max_steps,max_duration_seconds}`, which the vendor does not
publish, and keeps omitting the required `session_id`. Measured: the correct
upstream shape (`session_id`, no `agent_config`) is refused with
`TINYFISH_AGENT_HARD_CAP_REQUIRED`. That change restructures the paid gate and
needs its own decision — it is not in these patches.
