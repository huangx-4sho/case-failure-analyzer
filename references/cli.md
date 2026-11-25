# CLI workflows

**On this page:** [Audit](#audit) · [Inspect](#inspect) · [Compare](#compare) · [Validate and render](#validate-and-render)

Resolve the scripts from the installed skill directory. Examples use illustrative input and output paths. Reports and reviews consume saved records.

## Audit

| Input | Command |
|---|---|
| Task number or exact trial name | `python3 scripts/cfa.py audit task208 --jobs-root /path/to/benchmark --lang zh` |
| A specific job or trial directory | `python3 scripts/analyze_case.py --job /path/to/job-or-trial --task /path/to/task --output /path/to/audit --lang zh` |
| Latest matching trial per job and task in a benchmark | `python3 scripts/cfa.py audit --all --jobs-root /path/to/benchmark --lang zh` |

- Task-ID mode also accepts `python3 scripts/analyze_case.py --task-id task208` with the same root and language flags. When `--jobs-root` is omitted, it uses `CFA_JOBS_ROOT` if set. Otherwise it tries the current directory and its parent. Use a full trial name or the job selector `--job` to scope the match. See the [task-ID output layout](report-schema.md#task-id-quick-answer---task-id-task208).
- In path mode, `--job` and `--output` are required. Supply `--task` when discovery cannot determine the task definition. `--trial NAME` selects one trial within a job. The default is `--trial all`.
- A task-ID audit accepts `--report-dir` or `--output` to override the landing folder. With `audit --all`, an explicit destination is the batch root and job/task reports retain separate directories. Audit outputs must be outside the input roots. `--no-verdict-stdout` suppresses the task-ID card. The batch command suppresses cards by default.
- `--lang zh|en|bilingual` selects the report edition. Bilingual output includes a separate English edition. Use the [report schema](report-schema.md) to resolve report names for each mode.
- `--format both|json|markdown` controls written outputs. `--phase collect` writes evidence and candidate hypotheses. Use JSON or both in this mode. Use the default `--phase all` for attribution and reports.
- Multi-trial archives are lightweight. Use `--archive-evidence` when full per-trial evidence is needed. `--max-log-bytes N` sets the per-log read limit and defaults to 120,000. `--include-session-files` adds session files to the inventory.
- In path-mode job summaries, `recorded_event` gives the trial-specific rejection, interruption or review receipt and its source. `conclusion_level` and `category` describe the strength and ownership of the causal attribution. Use the cited field or log line to start a dossier when a writer, public requirement or effective input still needs review.
- Leave `--replay` at `none`. To rebuild a saved global board, run `python3 scripts/cfa.py index --report-root /path/to/failure-report`.

## Default deep review

Full audits investigate every rule-unknown trial. Each trial's `analysis.json` (or `data/analysis.json`) contains `causal_assessment.deep_review` with the captured rejection, audit fingerprint, public specification, submission slices, trajectory events, and scoring source. Collection-only mode still stops after evidence acquisition.

For a fixed validation cohort use `analyze_case.py --job JOB --trial-list TRIALS.txt --output AUDIT`, with one exact trial directory name per line. Missing or duplicate selections are errors. The evidence fingerprint tracks this trial and its sources across changes in sibling job statistics.

In a skill session use the same invoking model for the semantic comparison:

1. Run the normal audit with `--deep-review-mode host`. Read every `review_pending` packet. When excerpts are insufficient, inspect the original artifact, full tool arguments and applicable scorer function. For an archive cite the container and member. Compare the decision with plausible alternatives; a writer filename or a nearby failed check alone cannot identify the cause.
2. Prepare a JSON list containing one record per pending trial. Copy `trial_name` and `audit_binding` from that trial. Use `status: "traced"` only when the successful action and the effective submitted value explain the named rejection. With `status: "open"`, state the strongest observed discrepancy and exactly which original comparison, input or step is needed to distinguish the alternatives. Each record needs `mechanism`, `gap`, and `evidence`. The evidence keys are `public`, `submission`, `trajectory`, and `scorer`. Use `{"path":"…","quote":"…"}` for a file and `{"event":"trajectory:step:…:tool:…","quote":"…"}` for a tool action. File paths are relative to the task or trial. A submitted file starts with `artifacts/`. For an open result include the obtainable sources. Quote short exact substrings and name the actual expected and observed logic in the mechanism.
3. Rerun the same audit and destination with `--deep-review-mode host --deep-reviews /path/to/decisions.json`. Inspect the batch counts and finish pending or interrupted reviews before answering. The original rule category remains available alongside the final mechanism. Changed evidence or invalid citations invalidate reuse. The full [causal review](evidence-review.md#causal-review) is available for assigning exclusive responsibility or testing quantitative sufficiency.

A standalone CLI audit defaults to `--deep-review-mode auto`. It uses the installed, authenticated Codex CLI for new unknowns and submits bounded evidence excerpts to that model. File reading and citation checks run locally. A missing or failed reviewer is recorded as unfinished, separately from a reviewed evidence gap. `--deep-review-mode host` keeps the standalone process offline. Both modes reuse validated decisions from the same output directory.

## Inspect

Use an existing audit rather than recollecting evidence.

```bash
python3 scripts/cfa.py inspect task208 --jobs-root /path/to/benchmark --section errors
python3 scripts/cfa.py inspect task208 --report-dir /path/to/audit --section step --step 43
```

| Section | Use |
|---|---|
| `verdict`, `report` | Read the saved verdict card or full report |
| `errors` | Locate the interruption, rejection, matching scorer condition, or submitted field |
| `artifacts` | Inspect captured outputs, declarations, integrity checks, and verifier comparisons |
| `evidence`, `anchors`, `step` | Inspect the evidence digest, causal anchors, or a single tool step. Without `--step`, the step view shows recent events |
| `confidence`, `data` | Inspect the heuristic confidence breakdown or structured analysis |

Use `--job`, `--trial`, or an exact report directory if more than one saved audit matches. `--raw` selects the available unformatted view. Inspection applies credential and user-home path redaction by default.

## Compare

```bash
python3 scripts/cfa.py diff task208 task220 --jobs-root /path/to/benchmark
python3 scripts/cfa.py diff task208 --trials first-trial second-trial --report-dir /path/to/audit
```

Compare saved runtime states, root-cause candidates, failed checks, and causal anchors. Use `--job-a` and `--job-b` to scope separate targets. With one task and no explicit trial pair, the command selects the two most recent archived reports by modification time. Lightweight archives may omit comparison evidence. Both inspection and comparison accept `--help` for additional selectors.

## Validate and render

For refined attribution, follow [evidence review](evidence-review.md#causal-review) and keep the original audit as a source record. For schema validation or rendering a saved result, use the applicable evidence and analysis paths.

```bash
python3 scripts/validate_analysis.py --evidence /path/to/evidence.json --analysis /path/to/analysis.json
python3 scripts/render_report.py --evidence /path/to/evidence.json --analysis /path/to/analysis.json --output-report /path/to/report.md --lang bilingual
```
