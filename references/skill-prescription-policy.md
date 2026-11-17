# Skill Prescription Generation Policy

**On this page:** [Eligibility](#1-eligibility-gate-all-conditions-required) · [Remediation routing](#2-routing-table-for-non-eligible-cases) · [Prescription fields](#3-prescription-schema)

Per-trial `analysis.json["skill_prescription"]` remains null. Rule-based guidance is only a suspected proposal under `causal_assessment.capability`. A separate, read-only cross-case review can produce scoped guidance in `capability-assessment.json`; it does not install a skill or rerun calculations.


## 1. Eligibility Gate (All Conditions Required)

Advance a proposal to cross-case capability review **ONLY IF ALL 5 CONDITIONS HOLD**:

1. **Agent Root Cause**: `primary_root_cause.category == "agent"`.
2. **Non-Trivial Capability Gap**: The failure is NOT a one-off typo, transient shell mistake, or infrastructure hiccup.
3. **Cross-Case Reusability**: The missing knowledge applies across a family of scientific computing tasks (e.g., LAMMPS restart continuation rules, QE charge-density reuse across `scf` -> `bands`, CP2K SCF convergence recovery).
4. **Actionable Protocol**: The guidance can be expressed as a deterministic decision tree, pre-flight checklist, or diagnostic protocol.
5. **No Verifier Bug Encapsulation**: The prescription does NOT teach the agent to hack around a broken verifier regex or fabricate data.

## 2. Routing Table for Non-Eligible Cases

When any eligibility condition is false, do not confirm reusable guidance. Per-trial `skill_prescription` stays null in all cases; remediation is routed as follows:

| Primary Root Cause Category | Output Routing in `recommended_actions` | Eligible for cross-case guidance review? |
|---|---|---|
| `case` | Prompt / asset / `task.toml` specification fix | **No** |
| `infra` | Runner, Docker image mirror, network, timeout, or container fix | **No** |
| `verifier` | `tests/verify.py` parser/regex/tolerance patch + regression test | **No** |
| `unknown` | Evidence collection checklist | **No** |
| `agent` (reusable scientific/workflow gap) | Domain Skill prescription + Agent policy guidance | **Yes** |

## 3. Prescription Schema

The separate `review capability` request requires written `skill_prescription.guidance` and evidence reviews for independent tasks. See [existing-evidence review](evidence-review.md#capability-scope) for the executable request format. The following structure is a content guide when authoring a reusable skill from an eligible proposal:

```yaml
recommended_skill:
  name: "<kebab-case-skill-name>"
  trigger:
    - "<observable symptom or task pattern 1>"
    - "<observable symptom or task pattern 2>"
  capability_gap:
    - "<specific scientific or workflow reasoning gap observed>"
  required_guidance:
    - "<step 1 of diagnostic/execution checklist>"
    - "<step 2 of diagnostic/execution checklist>"
  anti_patterns:
    - "<what the agent did wrong that must be avoided>"
  evidence_cases:
    - "<case_id>:<event_ref>"
```
