# Research Core Reference Run

Generated 2026-11-19T11:28:46+08:00 from `benchmark/research-core/runs/suite-summary.json`.

Every provider below is a mock. These numbers are an offline baseline, not a product comparison: no live API was called and no key was used.

## Summary

- stages: 9
- cases: 175
- scored: 525
- not_configured: 0
- failed: 0
- timeout: 0
- invalid_output: 0

## Datasets

| stage | version | cases | dataset_checksum |
|---|---|---|---|
| brief | research-core-1.0.0 | 3 | `e129908a170db961124cfcbf0aff6a9edcb508f418138853cb754e3f04161632` |
| search | research-core-1.0.0 | 47 | `2526ff4c6c4ecc923c2fbb01129b5f115824742699b3a655ecb9a25319279e25` |
| fetch | research-core-1.0.0 | 33 | `ba0871a9f4d22e864ff9c54888b5f56109c3b9509eb03cfb6a74970e24a39949` |
| extraction | research-core-1.0.0 | 30 | `62780f3641fc08e414e29b9d743abc24310914447eedfac0e6a257bf0a4a0ce1` |
| qualification | research-core-1.0.0 | 23 | `5480d7afd328ea6f28149516a3b26beb234715c54f0ab9895806b8f68612cd7a` |
| identity | research-core-1.0.0 | 15 | `d6e81fce9a6a2248d9d007ab505d2b2756627ebde1f58fe94e14d8bd3b1168d3` |
| evidence | research-core-1.0.0 | 15 | `25f3350ed4f168167d970f3e7f5dc0c126aef40915bb179fdaf6bfb7cb55766d` |
| channel | research-core-1.0.0 | 6 | `f2183045dde1830d84138bb9e84ae9cbbed7beb019e73c632e47b26baa125c8e` |
| e2e | research-core-1.0.0 | 3 | `b126fc39f15f0b8294ee57233806fc00754d6c12ac38d6826a5047c09af6fd5f` |

## Per-stage results

### Product Brief Parsing

- provider: `mock_brief_rules`
- run_id: `rc-20261119T112735-99633b`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112735-99633b-mock_brief_rules.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 3 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 9 |

| metric | value |
|---|---|
| `coverage.channels` | 1.0000 |
| `coverage.exclusions` | 1.0000 |
| `coverage.icp_attributes` | 1.0000 |
| `coverage.pain_points` | 1.0000 |
| `coverage.personas` | 1.0000 |
| `coverage.queries` | 1.0000 |
| `forbidden_claims_leaked` | 0 |
| `overall_coverage` | 1.0000 |
| `repeat_identical` | True |

### Search

- provider: `mock_serper`
- run_id: `rc-20261119T112735-f1408e`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112735-f1408e-mock_serper.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 47 | 138 | 3 | 0 | 0 | 0 | 0 | 0 | 141 |

| metric | value |
|---|---|
| `correct_shape` | 1.0000 |
| `domain_hit_rate` | 1.0000 |
| `duplicate_rate` | 0.0000 |
| `excluded_hits` | 0 |
| `mrr` | 0.9787 |
| `precision_at_10` | 0.1087 |
| `precision_at_5` | 0.2174 |
| `recall_at_10` | 1.0000 |
| `repeat_identical` | True |
| `results` | 57 |
| `unique_discovery_rate` | 1.0000 |

**Known baseline gap.** `precision_at_5`/`precision_at_10` are low by construction: the lexical ranker returns up to ten results while most queries have one or two relevant companies. Recall, domain hit rate and MRR are the meaningful figures here.

### Fetch

- provider: `mock_local_http`
- run_id: `rc-20261119T112735-34177d`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112735-34177d-mock_local_http.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 33 | 84 | 15 | 0 | 0 | 0 | 0 | 0 | 99 |

| metric | value |
|---|---|
| `correct_no_content` | 1.0000 |
| `email_recall` | 1.0000 |
| `extraction_completeness` | 1.0000 |
| `false_success` | 0.0000 |
| `final_url_ok` | 1.0000 |
| `forbidden_hit` | 0.0000 |
| `link_recall` | 1.0000 |
| `marker_recall` | 1.0000 |
| `person_recall` | 1.0000 |
| `repeat_identical` | True |
| `text_length` | 173.5152 |

### Entity / Contact Extraction

- provider: `mock_extractor_regex`
- run_id: `rc-20261119T112736-ebeed2`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112736-ebeed2-mock_extractor_regex.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 30 | 90 | 0 | 0 | 0 | 0 | 0 | 0 | 90 |

| metric | value |
|---|---|
| `company_recall` | 1.0000 |
| `decision_maker_accuracy` | 1.0000 |
| `email_kind_accuracy` | 1.0000 |
| `email_recall` | 1.0000 |
| `forbidden_email_hits` | 0 |
| `gap_honesty` | 1.0000 |
| `invented_personal_emails` | 0 |
| `person_hallucinations` | 0 |
| `person_precision` | 1.0000 |
| `person_recall` | 1.0000 |
| `repeat_identical` | True |

### Qualification

- provider: `mock_qualification_rules`
- run_id: `rc-20261119T112736-28fd6c`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112736-28fd6c-mock_qualification_rules.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 23 | 69 | 0 | 0 | 0 | 0 | 0 | 0 | 69 |

| metric | value |
|---|---|
| `grounded` | 1.0000 |
| `icp_match_correct` | 1.0000 |
| `missing_evidence_recall` | 1.0000 |
| `relevance_error` | 0 |
| `repeat_identical` | True |
| `status_correct` | 1.0000 |
| `ungrounded_citations` | 0 |

### Identity & Deduplication

- provider: `mock_identity_rules`
- run_id: `rc-20261119T112736-8afd37`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112736-8afd37-mock_identity_rules.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 15 | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 45 |

| metric | value |
|---|---|
| `pair_f1` | 1.0000 |
| `pair_precision` | 1.0000 |
| `pair_recall` | 1.0000 |
| `relation_correct` | 0.9333 |
| `relation_false_positives` | 0 |
| `relation_misses` | 1 |
| `repeat_identical` | True |

**Known baseline gap.** `relation_misses=1` is deliberate. RC-IDNT-BRANCH-004 expects a `branch_of` relation between two same-name companies on different domains; the baseline will not assert a corporate relationship without a page that states it, so it reports nothing rather than fabricating one.

### Evidence & Provenance

- provider: `mock_evidence_checker`
- run_id: `rc-20261119T112737-ed3c2a`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112737-ed3c2a-mock_evidence_checker.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 15 | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 45 |

| metric | value |
|---|---|
| `contradictions_found` | 0 |
| `entity_correct` | 1.0000 |
| `fields_traceable_recall` | 1.0000 |
| `repeat_identical` | True |
| `sha256_correct` | 1.0000 |
| `snapshot_exists_correct` | 1.0000 |
| `stale_detected` | 1.0000 |

### Channel Evaluation

- provider: `mock_channel_rules`
- run_id: `rc-20261119T112737-f5efde`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112737-f5efde-mock_channel_rules.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 6 | 18 | 0 | 0 | 0 | 0 | 0 | 0 | 18 |

| metric | value |
|---|---|
| `ndcg` | 1.0000 |
| `order_match` | 0.6667 |
| `rejected_selected` | 0 |
| `repeat_identical` | True |
| `top1_correct` | 1.0000 |
| `top3_acceptable` | 1.0000 |

**Known baseline gap.** `order_match` is 2/6. The baseline sorts by the prior the brief states; the gold ranking is editorial and, in two cases, orders channels the brief rates equally. `top1_correct` and `ndcg` are the order-sensitive figures that do not depend on that judgement.

### Research E2E

- provider: `mock_research_e2e`
- run_id: `rc-20261119T112737-a9f1f1`
- manifest: `/home/fjd/projects/marketops-local-runtime-lab/benchmark/research-core/runs/rc-20261119T112737-a9f1f1-mock_research_e2e.manifest.json`

| cases | ok | empty | failed | not_configured | timeout | invalid_output | skipped | scored |
|---|---|---|---|---|---|---|---|---|
| 3 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 9 |

| metric | value |
|---|---|
| `dossier_complete` | 1.0000 |
| `email_presence_accuracy` | 1.0000 |
| `evidence_coverage` | 1.0000 |
| `fabricated_claims` | 0 |
| `forbidden_claims_seen` | 0 |
| `gap_honesty` | 1.0000 |
| `person_presence_accuracy` | 1.0000 |
| `prospect_recall` | 1.0000 |
| `prospects_expected` | 36 |
| `prospects_found` | 36 |
| `repeat_identical` | True |
| `status_accuracy` | 1.0000 |

## How to reproduce

```bash
python -m benchmark.research_core.cli validate
python -m benchmark.research_core.cli contract
python -m benchmark.research_core.cli suite --repeat 3
python -m benchmark.research_core.cli report --out RESEARCH_CORE_TEST_REPORT.md
```

## Public Evidence Benchmark

Dataset `research-core-public-1.0.0`. Real public pages, captured once with `robots.txt` honoured, desensitised on write, and served from `127.0.0.1:18091`. Scoring reads only the committed bytes.

- snapshots frozen: 56
- targets recorded as gaps: 44 (see `benchmark/datasets/research-core/public-evidence/GAPS.md`)
- companies: 33; creators: 17; countries: 11; languages: 5

| stage | provider | version | cases | scored | failed | not_configured |
|---|---|---|---|---|---|---|
| fetch | `mock_local_http` | research-core-public-1.0.0 | 56 | 168 | 0 | 0 |
| evidence | `mock_evidence_checker` | research-core-public-1.0.0 | 56 | 168 | 0 | 0 |

### Fetch (public)

- run_id: `rc-20261119T112829-15a8ad`
- dataset_checksum: `66e7c8921675b84ef0c20c3c9862286cbb56239720507e59173df413debd1d44`

| metric | value |
|---|---|
| `correct_no_content` | 1.0000 |
| `extraction_completeness` | 1.0000 |
| `false_success` | 0.0000 |
| `forbidden_hit` | 0.0000 |
| `marker_recall` | 1.0000 |
| `repeat_identical` | True |
| `text_length` | 101353.4464 |

### Evidence & Provenance (public)

- run_id: `rc-20261119T112837-aad16b`
- dataset_checksum: `1b4e919468f5ee030d0853df96c23f493b5de8c80e95354adc9915902f336e89`

| metric | value |
|---|---|
| `contradictions_found` | 0 |
| `entity_correct` | 1.0000 |
| `fields_traceable_recall` | 1.0000 |
| `repeat_identical` | True |
| `sha256_correct` | 1.0000 |
| `snapshot_exists_correct` | 1.0000 |
