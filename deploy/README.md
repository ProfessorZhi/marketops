# deploy/

Deployment definitions belong here; host-specific roots, secrets and production authority do not. The proposed target is described in `docs/DEPLOYMENT.md`; live acceptance is defined in `docs/MCP_SERVICE.md`.

## Required MVP bundle

Serper, Crawl4AI, Jev, Laya and a research worker runtime must all have working integrations and actual-use receipts. They are not deferred options. Individual jobs may avoid unnecessary browser/decision calls; missing required live acceptance still prevents a complete MVP verdict.

- `marketops`: one Python MCP application with Scene policy, SQLite ledger, evidence files, atomic admission/budgets and result ingestion.
- Research worker runtime: a deployment-provided bounded worker, wired through `MARKETOPS_RESEARCH_DELEGATE`, with verified research-completion integration. The plugin does not bundle a worker.
- Crawl4AI: a pinned isolated browser service on a private authenticated API, exercised for rendering and bounded multi-page acquisition.
- Laya: a reusable supervised inference process, colocated initially or a private sidecar when dependencies/resources require it; actual pinned checkpoint inference is required.
- Serper and Jev: authenticated external API adapters with per-call accounting.

Existing Hosting/Hermes stays unchanged. Claude Code is the first control client. Hosting later connects to the same remote MCP through an approved test profile, not a new RuntimeBackend.

A small Compose deployment or existing operations tooling is sufficient. Do not make Kubernetes, a Postgres cluster, a message broker, heavy OCR or a separate memory platform prerequisites. The number of integration names is not the number of public services.

## Deployment acceptance

Read current host governance, pin tested versions, keep operator UI private and expose only the authenticated business endpoint to intended clients. Internal container binding and host publication are distinct; do not inherit public exposure from examples.

Do not mount a Docker socket, full HOME, unrelated repositories or the whole credential vault into workers. Browser egress must reject internal/private targets even through redirects and subrequests. Research credentials cannot authorize mail or change policy.

Persist jobs, evidence and actual request/inference receipts independently of client sessions. Test atomic budget races, dependency blocking, duplicate submissions, stale attempts, crash recovery and reconnect. Unit fakes and readiness checks do not substitute for real provider/worker calls.

If a credential, model or runtime is missing, return a sanitized blocker and preserve partial research; do not silently declare full-MVP success. No application code, service, live model download, deployment manifest or production restart is created by this documentation change.
