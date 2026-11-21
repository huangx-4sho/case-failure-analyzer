# Agent mechanisms

Use `review.capability_dimension.mechanism_tag` to narrow a dimension when the
same record supports the proposed mechanism. Cite the applied decision,
applicable basis, and recorded outcome. Omit the tag when only the dimension
is supported. The review summary counts runtime leads separately from
evidence-supported capability diagnoses. A lead identifies an observation
to investigate, such as token usage near a limit or repeated image reads.

| Dimension | Mechanism tags |
|---|---|
| D1 `observable_definition` | `norm_or_metric_mismatch`, `reference_state_or_baseline_wrong`, `sign_or_unit_convention_wrong`, `derived_parameter_definition_mismatch` |
| D2 `physical_model` | `simplified_or_decoupled_model`, `protonation_or_state_enumeration_wrong`, `surrogate_model_inaccurate` |
| D3 `numerical_adequacy` | `fitting_model_or_residual_mismatch`, `sampling_sequence_or_seed_divergence`, `sampling_acceptance_rule_mismatch`, `convergence_or_grid_inadequate` |
| D4 `workflow_state` | `entrypoint_path_contract_wrong`, `stage_flag_or_ordering_wrong`, `replay_input_assumption_hardcoded`, `unfinished_remote_delivery` |
| D5 `execution_recovery` | `reasoning_token_runaway`, `context_overflow_by_large_read`, `unproductive_retry_or_timeout_burn` |
| D6 `postprocessing_provenance` | `identifier_normalization_loss`, `wrong_derived_column_mapping`, `evidence_or_hash_provenance_gap`, `output_schema_mismatch`, `source_operand_order_mismatch`, `reporting_scope_omission` |
| D7 `independent_validation` | `self_check_missed_nested_schema`, `self_check_reused_buggy_assumption`, `self_check_missed_stage_criterion`, `no_self_check_on_deliverable` |
| D8 `conclusion_calibration` | `feasible_set_overconstrained`, `cross_stage_evidence_overreach`, `statistical_applicability_or_claim_error` |
