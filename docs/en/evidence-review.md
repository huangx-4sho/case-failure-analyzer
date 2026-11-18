# Existing-evidence review

**[English](evidence-review.md) | [简体中文](../zh/evidence-review.md)** · [Project home](../../README.md) · [Guide](guide.md)

**On this page:** [Missing outputs](#missing-outputs) · [Recorded verification](#recorded-verification) · [Causal review](#causal-review) · [Capability scope](#capability-scope) · [Harness compatibility](#harness-compatibility)

This workflow reads the benchmark's saved artifacts. It does not run a verifier, simulation, shell command, or Docker container. A benchmark may already have performed independent recomputation; review the retained record and its coverage instead of repeating it or assuming every check recomputed a result.

## Missing outputs

Inspect `evidence.deliverable_audit` before interpreting a missing file:

| Status | What is established | Next evidence to inspect |
| --- | --- | --- |
| `present` | The declared path exists in the collected tree | File size, contents, schema, verifier checks; an empty directory may still be incomplete |
| `not_found_in_collected_tree` | An exact mapped path was absent from a complete bounded capture-tree scan | Runner collection records, the required output path, and actual write/readback events |
| `collection_failed` | Saved runner records indicate a collection fault | Which service/path failed and whether the verifier ran |
| `unresolved` | Mapping, declaration, root availability, permissions, or scan coverage is insufficient | Recover the existing manifest, correct the supplied paths, or obtain the missing saved logs |

`not_found_in_collected_tree` never means "the agent did not produce it." A write command is also not proof that the output reached the final collected directory. Compare these observations, retaining contradictions. Directory declarations do not imply particular child filenames. Service-specific declarations are kept unresolved without an established service mapping.

`verified_missing_outputs` records conservative exact-path matches from saved failed checks or verifier messages. A requirement alone, a similar basename such as `output.json.backup`, a missing child beneath a declared directory, and quoted/negated/example messages are not treated as proof that the declared path was missing. A collection declaration is distinct from a public requirement; agent-responsibility review must also inspect the public task instructions.

The scan defaults to 1,000 entries, eight directory levels, and 65,536 bytes per JSON read. It does not follow symlink mappings. Coverage limits, unreadable files, parse failures, checked paths, and empty outputs remain visible. File existence and parse success do not establish scientific correctness.

## Recorded verification

`causal_assessment.existing_verification` retains source files, failed checks, declared coverage, coverage limits, and numerical records. A reported recomputation, reference comparison, and generic `FAIL` are different evidence:

- Preserve reported values, actual delivered values, recomputed/reference values, tolerances, source pointers, and execution/artifact bindings.
- A reported delta is a deviation, not the delivered value or an absolute reference value.
- `verification_coverage` is the verifier's declaration of coverage. Missing detail does not establish independent recomputation.
- Saved comparisons support an outcome finding within their coverage. They do not by themselves identify the scientific decision responsible for the discrepancy.

No additional calculation is performed by this review. When records are insufficient, identify which existing files, checkpoints, task inputs, or verifier messages would resolve the gap.

## Causal review

Default audits produce `observed_failure` when a cause is unresolved, or `candidate_cause` for a rule-selected explanation. Passed trials use `not_applicable`. A separate structured review may reach `evidence_supported_cause`, meaning a this-trial explanation supported by the cited existing records and reviewer reasoning. It is not a guarantee of causal truth or a new experimental confirmation.

Generate an unfilled review record from a task-ID report:

```bash
SKILL_DIR="/path/to/skills/case-failure-analyzer"
REPORT_DIR="/path/to/benchmark/failure-report/example-job/task208"
python3 "$SKILL_DIR/scripts/cfa.py" review plan \
  --evidence "$REPORT_DIR/data/evidence.json" \
  --analysis "$REPORT_DIR/data/analysis.json" \
  --output /path/to/review-inputs/task208-review.json
```

Fill the record by reading existing sources. Supply a specific decision and its kind, an actual execution event, saved effective input/output references, an applicable requirement, a terminal outcome, and the causal reasoning connecting them. Review alternatives separately: task design, infrastructure, verifier behavior, implementation, measurement/post-processing, and stochastic variation. A primary explanation cannot simultaneously be excluded. Scientific/parameter decisions treat implementation mistakes as a competing explanation.

Keep unresolved items unresolved. The plan is not a form to complete by inventing reasons or ticking confirmation flags. Reasoning text and final answers are not execution evidence; absent files, recovered warnings, or generic failure records cannot substitute for the necessary decision/outcome binding.

Apply the reviewed record to a fresh separate output directory:

```bash
python3 "$SKILL_DIR/scripts/cfa.py" review apply \
  --evidence "$REPORT_DIR/data/evidence.json" \
  --analysis "$REPORT_DIR/data/analysis.json" \
  --review /path/to/review-inputs/task208-review.json \
  --output /path/to/reviewed-cases/task208
```

The bundle contains `evidence.json`, `original-analysis.json`, `analysis.json`, `review.json`, `report.zh.md`, and `report.en.md`. Inputs remain unchanged. JSON/source digest checks detect stale material; source freshness reads are bounded to 1 MiB per file and 8 MiB overall. Unchecked cited sources limit promotion. Structural validation checks references, bindings, review consistency, and attribution rules; it cannot decide whether a domain interpretation is scientifically sound.

A review may optionally provide `proposed_attribution` with `category`, `subtype`, a canonical `code`, and authored `summary_en` / `summary_zh`. The category must agree with `decision.kind`. Verifier, case, and infrastructure reviews can anchor the decision to a typed saved verifier, contract, or runtime observation; agent-cause reviews still require an execution event. All reviews require cited effective inputs, outputs, applicable requirements, terminal observations, and examination of competing explanations.

The original analysis snapshot is retained so validation can reconstruct the change. A reviewed attribution does not alter the benchmark verdict or reward. Any retained numerical confidence is explicitly the **original rule evidence score**, not confidence in the review. Freshness checks cover the cited sources (and the trajectory source when used); growth of an unrelated shared job log does not invalidate a completed trial's review. Unchecked cited sources remain a limitation.

### Failure location and self-check evidence

The report, verdict card and `inspect --section errors` list the runtime interruption and the scoring rejection separately. When a message matches a single location in the scorer, the report gives the line and any condition it can identify. It also shows the submitted value when the field resolves to a unique captured file and scalar. Completeness scores retain their original scope. The per-check records are in `contract_review.failure_locations`. The field `causal_assessment.failure_detail` holds the records used in the report.

To review a self-check, look up the command, script version and output in `causal_anchors.self_check_executions`. Compare the criterion it tested with the public requirement and the recorded rejection. One script can pass an interface check and fail a numerical check. If the generator made an error and the self-check missed it, record the two decisions separately.

For a larger cited file, add `--hash-source art:trajectory_json` to `review plan`. Repeat the option for each artifact ID that has a collected digest. Both plan and apply read the file in chunks and verify its collected SHA-256. The additional budget is 32 MiB per selected file and 128 MiB across those files. A missing digest, changed file or unresolved alternative limits the conclusion.

## Capability scope

For a failed agent trial, `review plan` offers an optional `capability_dimension`. Inspect the saved decision and select one of the [eight dimensions](taxonomy.md#agent-capability-dimensions). In the review JSON, supply `dimension`, `rationale_zh`, `rationale_en` and `evidence_refs`. Cite the decision event plus references from its basis and outcome. Use the rationale to explain the mechanism and which adjacent interpretation the evidence distinguishes. The event may be the tool call that wrote the submitted claim. Each review covers one decision.

When the sources cannot distinguish the mechanism, supply `{"status":"insufficient_evidence","rationale_zh":"…","rationale_en":"…"}` with the missing evidence. Leaving the field null defers annotation. The resulting `causal_assessment.capability.diagnosis` distinguishes `needs_review`, `insufficient_evidence`, `candidate` and `supported_in_trial`. An automatic trace match can only produce a candidate. A reviewed dimension inherits the support ceiling of its bound causal decision. The field is absent on other root categories and may be absent in older reports. The parent `capability.status` separately tracks proposed reusable guidance.

To review a specific dimension across tasks, include its ID as `dimension` in the cross-task request. Every supplied task must have a supported decision bearing that dimension, and the reviewer must establish a shared mechanism.

One failed trial, even after review, cannot establish a reusable scientific capability deficit. Per-trial `skill_prescription` stays null; heuristic guidance is retained only as a suspected proposal.

`cfa.py review capability --review <request.json> --output <capability-assessment.json>` reads existing reviewed bundles and produces a separate scoped capability assessment. The request uses `schema_version: "cfa-capability-review-v1"`, `reviewed_bundles`, `reviewer`, a `skill_prescription` object containing written `guidance`, and `generality_review` entries for:

- `shared_mechanism` and `task_independence`, each covering all supplied case IDs;
- `reusable`, `nontrivial`, `no_verifier_workaround`, `no_answer_substitution`, and `scope_limits`.

Each entry contains `rationale` and `case_ids`. At least two distinct task and source-content identities with supported agent-cause reviews are required. This is a minimum evidence constraint, not a statistical accuracy guarantee. The resulting claim remains `supported_in_reviewed_cases`; it does not establish a universal deficit of the model.

## Harness compatibility

The client invoking the skill and the format of the trajectory being analyzed are separate concerns. Any agent with file access and Python can follow `SKILL.md`; native discovery and invocation syntax depend on the client.

Recognized trajectory structures include ATIF, OpenAI chat messages, Anthropic blocks and Claude Code envelopes, OpenAI Responses tool items, and Codex rollout response items. `trajectory_normalization` records format, coverage, diagnostics, and pointer space. Converted `/steps/...` pointers address normalized steps, not raw JSONL source lines. Unknown records, truncated input, unmatched results, or missing results remain partial coverage; they are not evidence that the agent skipped an action.
