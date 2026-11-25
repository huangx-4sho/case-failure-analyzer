---
name: case-failure-analyzer
description: Audit scientific-computing benchmark failures from saved job artifacts, task requirements, verifier code, and agent trajectories. Locate the recorded rejection or interruption and assess responsibility across Agent decisions, Verifier rules, Case design, and Infrastructure. Review specific failure mechanisms and cross-task patterns when requested.
metadata:
  short-description: Audit scientific benchmark case failures across 4 peer categories with concise reports
---

# Case failure analyzer

Use Python 3.9+ and resolve `scripts/` and `references/` from this skill's directory. Supply benchmark and report locations at invocation time.

Read saved evidence. Do not run a verifier, simulation, Docker container, or replacement calculation. Keep benchmark inputs read-only and use the audit's output isolation checks.

## Quick audit

For a task number or trial reference, start with the audit and its printed verdict card.

```bash
python3 scripts/cfa.py audit task208 --jobs-root /path/to/benchmark --lang zh
```

Select `zh`, `en`, or `bilingual` for the requested report language. The task number selects the newest matching trial. The card is written to `summary.md` alongside the full report and `data/`. Use the printed `REPORT_DIR` to find and link the requested edition.

Lead with what happened to the specific submission. Give the affected field, calculation, script line or interrupted operation, the recorded expectation and observation, and their source. Report the responsible party and conclusion level separately. An `unknown` rule result opens an investigation.

For an unexplained rejection, follow the failed check into its native diagnostic. Compare the effective submitted value and its writer with the public contract and the scorer condition. For a numerical mismatch, trace where the two calculations first differ (for example in sign, units, selection, rounding or serialization). For a timeout, inspect the last timed actions, outcomes and missing deliverables. Report the most specific link the records establish. Identify the exact missing link and competing explanations when the earlier cause stays open.

For a batch, the invoking agent completes [default deep review](references/cli.md#default-deep-review) during this request. Run the first audit with `--deep-review-mode host` to use this session. For every rule-unknown trial, read its four-source comparison, follow the original files where excerpts end, and bind the earliest actionable discrepancy to a successful tool step and the recorded rejection. Submit all remaining decisions in one bound review file and rerun the audit to publish them. Reuse validated decisions on later runs. Report the number traced, the number reviewed with specific missing evidence, and any unfinished reviews separately. A prepared comparison packet is unfinished work. A traced mechanism explains the cited check; responsibility across Agent, Case and Verifier is a separate determination.

## Evidence boundaries

- Locate the recorded stop or rejection and its source line or field before assigning responsibility. Keep runner interruptions, scoring outcomes, and submission-only checks separate. Use `score_produced` to establish whether a final score exists.
- A pre-startup failure is an infrastructure candidate when a fatal setup fault is recorded. Otherwise preserve the cause as unknown. A pre-verifier collection fault can explain the stop even when a required artifact is missing.
- Agent attribution requires a concrete execution action or deviation linked to the public requirement, effective inputs or submission, and recorded outcome. A generic failure, missing capture, or partial trajectory leaves that link unresolved.
- Treat static scorer matches, reconstructed source, and numerical differences as leads. Verify applicability to the saved run. Distinguish a recorded result, a rule-selected candidate, and an evidence-supported review.

## Read only the reference needed for this request

When a link names a section, start with that section and follow its references as needed.

| Request or evidence | Read |
|---|---|
| Audit a job or trial path, run a batch, inspect a saved audit, or compare trials | [CLI workflows](references/cli.md) |
| Locate a specific decision or review an unresolved Agent candidate | [Dossier and causal review](references/evidence-review.md#dossier-for-a-specific-decision) |
| Investigate a missing deliverable, an isolated replay, or an apparent contract mismatch | [Missing outputs](references/evidence-review.md#missing-outputs) or [contracts and replay](references/evidence-review.md#contracts-and-replay), as applicable |
| Explain why a gate selected or deferred a category | [Attribution protocol](references/attribution-protocol.md#2-causal-precedence-timeline--gate-evaluation-order) |
| Diagnose a capability or aggregate completed reviews | [Capability scope](references/evidence-review.md#capability-scope) or [review summary](references/evidence-review.md#review-summary) |
| Check output fields, report layout, or a software-specific failure signature | [Reference index](references/README.md) |

For a specific mechanism, use the dossier to bind the relevant action, requirements, artifacts, and alternatives. Apply the structured review when the sources support it. In the answer distinguish the verified defect, quantitative sufficiency, and overall attribution. Name the missing evidence when the cause remains open.
