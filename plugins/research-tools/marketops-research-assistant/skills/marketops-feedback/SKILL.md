---
name: marketops-feedback
description: Self-improvement loop for MarketOps. Use during every research session to record blockers and the user's corrections as they happen, and at the end of a session (or when the user asks what went wrong / 遇到什么问题 / 反馈) to build the feedback ZIP the user sends to the plugin maintainer.
---

# MarketOps feedback (自进化)

The plugin gets better only from what you write down while working. Chat history is lost when the
conversation ends; notes in the shared data directory are kept and collected from every
conversation on this computer.

## Record as it happens: `marketops_note`

Call it the moment one of these happens, without asking the user first and without stopping the
research afterwards:

| What happened | category |
|---|---|
| The user corrects you (a rule, a definition, a count, "that is not a customer", "switchboards don't count") | `human_correction` |
| The user has to repeat an instruction ("don't stop", "keep going", "which number is right?") | `human_correction` |
| A site blocks, shows a challenge/login, or changed so the expected page is gone | `source_blocked` |
| A provider fails: no key, no credit, refused, timed out, unexpected schema | `provider_blocked` |
| You found a contact but the tool refused to save it, or you could not quote it | `contact_found_not_stored` |
| A file was produced but not delivered (handoff failed, empty sheet) | `artifact_without_delivery` |
| You cannot get evidence you can re-read for a claim | `evidence_missing` |
| Anything else that cost time | `other` |

Write `problem`, `action_taken` and `observed_result` in one plain sentence each; put your guess at
the cause in `hypothesis`. No URLs, emails, names of people or secrets (host names of blocked sites
are fine). `evidence_ref` is a saved artifact or event ID from this Run (from the tool result you
were looking at); only `human_correction` may leave it empty. Use a new `note_id` each time (e.g.
`n-<stage>-<counter>`).

A user's correction is also a rule for the rest of this Run: apply it immediately to the remaining
targets and to records already written (supersede them), then continue.

## Use what was learned before

At the start of a task, `marketops_start` / `marketops_next` return relevant earlier lessons and
`marketops_experience_recall(run_id, stage)` gives more. Apply them, then report whether they helped
with `marketops_experience_feedback`.

## End of every session: `marketops_feedback_pack`

After `marketops_handoff` (or when stopping for any reason), call
`marketops_feedback_pack(reporter="<the user's name if known>")`. Tell the user, in their language:

> 本次的问题和经验已整理成反馈包：`<zip path>`。请把这个文件发给插件维护者，用于改进插件；里面不含邮箱、网址或密钥。

Also tell them which of the providers (Serper, SerpApi, DeepSeek, TinyFish, Crawl4AI, Jev) were not used
this session and why, from the pack's `unused_providers`.
