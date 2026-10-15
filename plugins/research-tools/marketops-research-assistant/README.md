# MarketOps Research Assistant — v0.4.0 Candidate (NOT a customer release)

MarketOps has two deliverables: (1) this human-led, MCP+Skills market research plugin for Codex/WorkBuddy and later DeepSeek Harness; (2) a separate full autonomous Claude Code workflow to build only after the research process, data model and client experience have been verified.

**This is a GitHub review candidate, not an installation-ready ZIP.** Do not give Member-A an archive claiming full success before running RELEASE_GATE on local Codex + WorkBuddy. No credentials or paid provider calls were made while preparing this branch.

## What the 16 Skills cover

- **8 Member-A business Skills**: one research entry, intake/Scene, source discovery, venues/organizations and their responsibilities, people/roles, Evidence, Excel, human-review handoff.
- **6 provider Skills**: a tool router plus SerpApi, Serper, Crawl4AI, TinyFish and Jev. A live provider is optional per task; five vendors are not five mandatory sequential steps.
- **1 retrospective Skill**: record real bottlenecks, successful methods, wrong assumptions and corrections; generate candidate experience lessons with provenance, replay and human approval.
- **1 setup Skill**: onboard Codex or WorkBuddy in the customer's chosen local directory without silently enabling any paid calls.

Member-A's business basis is recorded and bounded in [MEMBER_A_SKILL_ANALYSIS](references/MEMBER_A_SKILL_ANALYSIS.md), [BUSINESS_PLAYBOOK](references/BUSINESS_PLAYBOOK.md), [QUALITY_GATES](references/QUALITY_GATES.md) and [EXPERIENCE_PROTOCOL](references/EXPERIENCE_PROTOCOL.md). A Skill never fills a known UK SOP gap (e.g. strongly bound named procurement role) by silently changing A/B/C/D grades.

## One five-provider Lite Research MCP (new candidate code)

The root plugin.json + mcp.json (new portable format) and compatibility .codex-plugin/plugin.json + .mcp.json reference one **MarketOps Lite Research stdio MCP**. Implemented in scripts/research_mcp.py with one local customer-owned SQLite staging store, append-only event journal and source material artifacts.

| Provider | Current callable ability inside this one MCP | What's **not** implemented or verified yet |
| --- | --- | --- |
| SerpApi | Google Web/Maps discovery through official REST adapter, with Place ID and URL candidates | Full official MCP multi-engine tools still optional separately; real key test not done |
| Serper | Google Web/Places discovery through official API adapter | Live authenticated test not done; no automatic double charge/fallback |
| Crawl4AI | Local AsyncWebCrawler or official Cloud /scrape; saves extracted material as unverified artifact | Local Chromium and Cloud Key not tested on Member-A's machine; bulk/extract Cloud MCP separate |
| TinyFish | Official Search and Fetch API through TINYFISH_API_KEY | Advanced browser Agent and OAuth MCP separately installed and human-authorized |
| Jev / TypeSafe | Official typesafe-sdk Choice over bounded source text, returns a judgment artifact | Noul/Score not yet exposed as MCP tools; model and SOP verification not proven |

The standalone older dual_search_mcp.py stays as an internal search component. **Do not install both dual-search and the new research_mcp simultaneously**. The user sees only one MarketOps Research MCP in the new package.

## Stage flow, data, and experience

~~~text
ResearchBrief (human-approved scope and business policy)
    -> Search receipts and source candidates (Serper OR SerpApi / TinyFish)
    -> Crawl/Fetch derived materials and source artifacts
    -> Candidate VenueAsset / Account / relationship
    -> Per-Scene qualification suggestions + review queue
    -> Candidate Person / Department / RoleAssignment
    -> Literal public contact signal candidates
    -> Formal route review and official Excel/CSV handoff (pending business engine)
               |
               +-- every managed operation -> append-only Event Journal
                         -> incident/experience candidate -> replay -> human approve
~~~

The local Lite database is **a research candidate and handoff staging store**, NOT MarketOps canonical production marketops.db and NOT an authenticated human-reviewed A/B/C/D promotion engine. All source results or Jev output remain candidates even after a tool reports succeeded. No outreach is available.

The runtime root is MARKETOPS_RESEARCH_DATA_ROOT, customer-owned, outside Git:
- state/research-lite.sqlite3 — Runs, candidate facts and artifact references.
- state/research-events.sqlite3 — started/succeeded/unknown operation events.
- state/dual-search-ledger.sqlite3 and state/provider-reservations.sqlite3 — external attempt reservations, not dollar budgets.
- artifacts/ and search-receipts/ — recorded provider-derived materials and hash.
- exports/ — candidate-only CSV; a true Excel workbook requires client Excel tooling and evidence inspection.
- experience/ — unapproved incident/lesson draft (historical measurements immutable).

See [DATA_HANDOFF](references/DATA_HANDOFF.md), [STORAGE_AND_TOPOLOGY](references/STORAGE_AND_TOPOLOGY.md) and [MCP_TOOL_CATALOG](references/MCP_TOOL_CATALOG.md). A shared file SQLite must never be used by two computers over SMB; remote use needs one authenticated server instance.

## First read for Member-A

**[MEMBER_A_INSTALL — agent-assisted Codex / WorkBuddy installation](references/MEMBER_A_INSTALL.md)** gives the full operator-facing procedure and a copyable prompt. The installer is scripts/setup_clients.py: defaults to dry-run, creates a separate Python virtual environment on explicit --apply, and only downloads package dependencies when --install-deps is explicitly selected. Customers apply for their own five credentials and enter them locally. The script cannot register API accounts or open provider billing.

Quick offline plan:

~~~bash
python scripts/setup_clients.py --destination /CUSTOMER/LOCAL/MARKETOPS-RESEARCH-v040 --client both
~~~

After local authorization to install files and PyPI dependencies, use --apply --install-deps. The installed Codex marketplace and the WorkBuddy MCP+Skills connector are generated from the same source; WorkBuddy must be rebuilt on the target machine so its Python path is valid. Do not pass actual keys to the installer.

Default external access is OFF: MARKETOPS_SEARCH_ALLOW_NETWORK=0, MARKETOPS_SEARCH_MAX_CALLS=0, MARKETOPS_RESEARCH_ALLOW_NETWORK=0, MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS=0. Each actual request requires a bounded Run, deliberate host/domain allowlist (for fetched pages), selected provider and user-controlled credentials.

## What about the Fengjiang model gateway and Crawl4AI?

Crawl4AI executes HTTP/Chromium retrieval. The company LiteLLM model gateway cannot replace the browser. Once crawler output exists, an approved and OpenAI-chat-compatible gateway may later supply a model to Crawl4AI's optional LLMConfig(provider, base_url, api_token). Its concrete internal URL/model/Key have not been confirmed and are **not bundled or enabled**. First pilot may use Crawl4AI Cloud for fast client setup or local Crawl4AI for sensitive customer data after Chromium setup. Read [MEMBER_A_INSTALL](references/MEMBER_A_INSTALL.md).

## Verification, test status and next release gate

**Authored:** offline synthetic tests for dual-search, provider bridge, Lite store, event journal, installation scripts, source-vendor separation and rules rejecting false promotion. **Not yet run on an authorized Python runner as a full suite:** tests; not yet live-tested in Codex/WorkBuddy or billed Provider endpoints. Underlying PR may have unrelated failing CI; never treat source readback as passing an execution test.

**[RELEASE_GATE](references/RELEASE_GATE.md)** must pass before preparing Member-A's versioned source ZIP. Specifically, the customer-facing promise of verified field contacts requires integrating the existing MarketOps business domain's human/SOP route promotion and proving end-to-end export; our local candidate tools do NOT currently do that.

We keep this PR Draft and do not deploy anything to Tokyo, change company LiteLLM gateway settings, buy credits, store API keys or send emails. Member-A's actual credentials and Windows client install remain owned by Member-A.
