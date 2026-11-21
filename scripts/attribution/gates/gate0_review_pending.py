"""An explicit unscored review request has no failure cause to attribute."""

from confidence import attach_confidence_metadata


def gate0_review_pending(ctx):
    pending = ctx.runtime.get("verdict") == "pending" and ctx.runtime.get("reward") is None
    ctx.trace.record(
        gate_id="gate0_review_pending",
        matched=pending,
        reason="saved verifier requires review without a final score"
        if pending
        else "no unscored review request",
        checks={"verification_status": ctx.verification_status},
    )
    if not pending:
        return None
    summary = "The verifier requested independent review. The final verdict is pending."
    if ctx.runtime.get("reward_scope") == "evidence_completeness_only":
        summary = (
            "The recorded reward measures evidence completeness. Scientific review is pending."
        )
    return {
        "schema_version": "failure-analysis-v1",
        "case_id": ctx.case_id,
        "trial_name": ctx.trial_name,
        "verdict": "pending",
        "failure_stage": "none",
        "detection_stage": "verifier_execution",
        "first_unrecovered_deviation": None,
        "failure_manifestation": {
            "type": "review_required",
            "summary": summary,
            "scoring_reached": False,
        },
        "primary_root_cause": attach_confidence_metadata(
            {
                "category": "none",
                "subtype": "review_required",
                "code": "REVIEW_REQUIRED",
                "summary": summary,
            }
        ),
        "contributing_factors": [],
        "competing_hypotheses": [],
        "evidence_refs": ["art:trial_result"],
        "excluded_hypotheses": [],
        "recommended_actions": [
            {
                "owner": "Review",
                "action": "Resolve the saved verifier review request using the retained submission and review criteria.",
            }
        ],
        "skill_prescription": None,
    }
