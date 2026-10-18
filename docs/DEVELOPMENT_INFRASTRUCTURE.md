# Development infrastructure — shared Laya, Serper and Jev

Status: user-confirmed development topology / target implementation, 2026-09-22. This narrows the current deployment phase without reducing the longer-term MarketOps product. Docker packaging and production Hosting integration are deferred, not prerequisites for development acceptance.

## 1. One shared local model, independent applications

```text
ServerOS adapters/Skills  ----+
                             +--> one governed Laya service on Tokyo
MarketOps application/MCP ----+    one process / one resident checkpoint
       |
       +--> Jev hosted API when explicitly configured and data-safe
       +--> Serper URL discovery -> allowed HTTP/Crawl4AI -> evidence
       +--> existing Quay / Claude research runtime
```

MarketOps keeps its own DecisionProvider interface, authenticated single business MCP, ledger, Scene/SceneRun policy, budgets and result ingestion. It does not import ServerOS source, share Work Engine SQLite, or require ServerOS's daemon for every decision call. A stopped MCP client does not stop the shared Laya process.

Reusable inference implementation/protocol is owned in ability-management; instance configuration and secret references belong to the governed host setup. See its design/SHARED-PROVIDER-CONTRACT.md and ServerOS docs/shared-decision-search.md. Market-specific DecisionSpecs, evidence and grades remain here.

## 2. Reuse actual code and configuration

Current product repository is ProfessorZhi/marketops. Read latest main and the actual active implementation branches; do not begin new implementation from the stale marketops-research-pipeline checkout. Do not move/rename/reset the active repository-convergence standalone exception.

SerperSearchProvider, SearchQuery/Result and Settings already exist. This turn located a nonempty SERPER_API_KEY in the current user's protected vault with mode 0600; it did not test the remote account or publish the value. Use the existing secret injection path, never source an untrusted file or put keys into repo/.env.example/logs/artifacts/client prompts.

Retain the single Settings entry for environment configuration. Add only needed endpoint, credential-ref, timeout, provider selection and DecisionSpec settings. Do not use filesystem imports from ServerOS or start a local Laya model automatically if the shared endpoint is down.

## 3. Decision integration in MVP

Both Laya and Jev adapters remain in MVP integration scope. Jev is remote, not another local service; if its authorized binding is absent, contract tests can complete while the real comparison remains UNBOUND. Do not delete that requirement or fabricate live results; ordinary research need not wait for it.

Start in shadow on one bounded semantic DecisionSpec, e.g. role/function relevance given fetched evidence and the scene rubric. Pin input/spec/model/tokenizer/calibration revisions and record independent labels, unknowns, errors, latency and actual request accounting. Raw provider probabilities are not comparable confidence guarantees without measured normalization/calibration.

Use one compatible resident checkpoint for the initial shared service. The previously tested English-root ONNX configuration had a 512-token limit; do not submit Chinese or long pages assuming multilingual support. Evaluate a supported multilingual variant for mixed workloads, or explicitly abstain/escalate unsupported inputs. Do not preload all model variants by default.

Domain rules remain final: literal email must occur in the claimed fetched page/block with valid entity/function binding; no-email is RouteOpportunity/ContactSignal, not a fabricated email grade. Models cannot override research_only=true, send_authorized=false, suppression, budgets, access controls or SOP A/B/C/D/X.

Shadow processing must not secretly discard low-scored candidates or persist suggestions as verified facts. Advisory activation is per DecisionSpec after held-out tests; high confidence alone is not sufficient. Do not send internal/private ServerOS material to Jev through automatic fallback.

## 4. Search and acquisition

Serper supplies candidate URLs; snippets remain discovery metadata, never final identity/email evidence. Reuse the existing adapter and legal HTTP-first/Crawl4AI escalation. A successful HTTP response does not justify launching a browser unless the declared acquisition outcome/policy requires it; 403/WAF is not permission to evade access controls.

Preserve request-unique or content-addressed raw responses. Current query-slug capture should be reviewed before concurrent experiments: repeating the query must not overwrite evidence for a prior SceneRun. Reserve budget before each external attempt, settle results/unknown attempts correctly, and test concurrent accounting rather than using an unlocked counter.

Sharing a credential with another developer process does not provide a shared quota automatically. Use existing quota controls or allocate bounded consumer budgets with a known aggregate; do not claim account-wide enforcement unless measured. Keep private queries out of logs and third-party inference payloads.

## 5. Resource limits and local endpoint

One model service, lightweight clients. Initial planning defaults: CPU-only, 4 inference threads, one running inference, bounded/fair queue and OS resource ceiling. The host is shared; total CPU/RAM is not a per-user grant. Validate the actual model's peak RAM, cold/warm behavior, batch size, sustained queue and fairness under the configured quota.

No public model port; use a controlled Unix socket or authenticated loopback endpoint. Request consumer/tenant labels are not authentication. Namespace/caching/rate limits must protect one project's context from another and prevent large MarketOps batches starving small ServerOS requests.

If Laya is unavailable, shadow may be skipped with an explicit observation or an already-authorized native judgment used. Never launch extra models per worker, silently switch to Jev, or turn provider failure into an accepted business result.

## 6. One MarketOps MCP, not one MCP per vendor

Keep control-client and task-scoped worker views inside the same business service as defined by MCP_SERVICE.md. Serper/Laya/Jev/Crawl4AI are adapters or internal calls; expose a bounded research tool only where a real worker needs it. Do not expose host vault paths, arbitrary model URLs, unrestricted shell or recursive dispatch to research workers.

Quay acceptance must use actual research worker outcomes and domain validation. A task whose only command is a unit-test runner demonstrates a gate, not end-to-end autonomous Account research. One driver owner and scoped task/process mapping remain required.

## 7. Acceptance before any Docker work

1. Configure MarketOps and a ServerOS client to call the same Laya service instance/model digest; inspect process count and bounded memory.
2. Run a small real authorized research cohort through the business MCP and genuine Quay/Claude worker; Serper URL discovery, fetched evidence, validated route/opportunity and result receipt are traceable.
3. Evaluate the same frozen evidence/DecisionSpec with local Laya and authorized Jev; if Jev is unbound, report that exact incomplete experiment rather than block all research.
4. Check language mismatch, overlong input, malformed output, high-confidence errors, overloaded queue, service failure and no-private-data remote fallback.
5. Repeat request/reconnect/service restart preserves job/result identity; two consumer workloads respect quota and namespace boundaries.

Do not create Docker/Compose, host networking, GPU configuration or a second Laya deployment in this phase. Retain endpoint injection and standalone client contract tests so later containerization is a deployment change rather than a rewrite. Existing Hosting source remains unchanged; all outreach/send functionality remains disabled.
