# Script map

## Workflow commands

| Task | Script |
| --- | --- |
| Audit a task, review saved evidence, inspect or compare reports, and rebuild the index | [`cfa.py`](cfa.py) |
| Audit an explicit job or trial path | [`analyze_case.py`](analyze_case.py) |
| Validate saved evidence, analysis, or report files | [`validate_analysis.py`](validate_analysis.py) |
| Render a Markdown report from saved JSON | [`render_report.py`](render_report.py) |
| Export a portable copy of the skill | [`export_skill.py`](export_skill.py) |

## Modules by responsibility

| Responsibility | Modules |
| --- | --- |
| Resolve trials, discover artifacts, and determine runtime state | [`task_ref.py`](task_ref.py), [`discover_artifacts.py`](discover_artifacts.py), [`runtime_state.py`](runtime_state.py), [`extract_runtime_errors.py`](extract_runtime_errors.py) |
| Normalize agent activity and trace provenance | [`normalize_trajectory.py`](normalize_trajectory.py), [`self_check_execution.py`](self_check_execution.py), [`trace_causal_anchors.py`](trace_causal_anchors.py) |
| Collect verifier records, check statistics, and score dependency evidence | [`normalize_verifier_report.py`](normalize_verifier_report.py), [`verify_check_stats.py`](verify_check_stats.py), [`scoring_dependencies.py`](scoring_dependencies.py) |
| Audit declared outputs and task contracts | [`deliverable_audit.py`](deliverable_audit.py), [`audit_contract.py`](audit_contract.py), [`contract_review.py`](contract_review.py), [`attribution/failure_location.py`](attribution/failure_location.py) |
| Match scientific errors and compare definitions | [`extract_scientific_errors.py`](extract_scientific_errors.py), [`scientific_contract_review.py`](scientific_contract_review.py) |
| Score hypotheses and apply attribution gates | [`generate_hypotheses.py`](generate_hypotheses.py), [`confidence.py`](confidence.py), [`attribution/engine.py`](attribution/engine.py) and [`attribution/gates/`](attribution/gates/) |
| Review causal evidence and diagnose capabilities within a trial | [`review_cause.py`](review_cause.py), [`causal_assessment.py`](causal_assessment.py), [`attribution/capabilities.py`](attribution/capabilities.py) |
| Render report views and publish bundles | [`report_style.py`](report_style.py), [`render_report.py`](render_report.py), [`render_verdict.py`](render_verdict.py), [`render_batch.py`](render_batch.py), [`render_index.py`](render_index.py), [`staging.py`](staging.py) |
| Share CLI validation and structured diagnostics | [`cli_args.py`](cli_args.py), [`diagnostics.py`](diagnostics.py) |

See the [skill workflow](../SKILL.md) and [reference index](../references/README.md) for operating instructions and field definitions.
