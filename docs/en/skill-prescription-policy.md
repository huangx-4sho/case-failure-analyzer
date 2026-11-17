# Skill Prescription Generation Policy

**[English](skill-prescription-policy.md) | [简体中文](../zh/skill-prescription-policy.md)** · [Project home](../../README.md) · [Guide](guide.md)

**On this page:** [Eligibility](#1-eligibility-gate-all-conditions-required) · [Remediation routing](#2-routing-table-for-non-eligible-cases) · [Prescription fields](#3-prescription-schema)

`case-failure-analyzer` optionally generates `skill-prescription.md` (and populates `analysis.json["skill_prescription"]`). To prevent polluting the agent's skill library with brittle workarounds or single-case patches, strict eligibility criteria apply.

## 1. Eligibility Gate (All Conditions Required)

Generate a `skill-prescription.md` **ONLY IF ALL 5 CONDITIONS HOLD**:

1. **Agent Root Cause**: `primary_root_cause.category == "agent"`.
2. **Non-Trivial Capability Gap**: The failure is NOT a one-off typo, transient shell mistake, or infrastructure hiccup.
3. **Cross-Case Reusability**: The missing knowledge applies across a family of scientific computing tasks (e.g., LAMMPS restart continuation rules, QE charge-density reuse across `scf` -> `bands`, CP2K SCF convergence recovery).
4. **Actionable Protocol**: The guidance can be expressed as a deterministic decision tree, pre-flight checklist, or diagnostic protocol.
5. **No Verifier Bug Encapsulation**: The prescription does NOT teach the agent to hack around a broken verifier regex or fabricate data.

## 2. Routing Table for Non-Eligible Cases

When any of the 5 eligibility conditions is false, `skill_prescription` MUST be `null`, and remediation is routed as follows:

| Primary Root Cause Category | Output Routing in `recommended_actions` | Generate `skill-prescription.md`? |
|---|---|---|
| `case` | Prompt / asset / `task.toml` specification fix | **No** |
| `infra` | Runner, Docker image mirror, network, timeout, or container fix | **No** |
| `verifier` | `tests/verify.py` parser/regex/tolerance patch + regression test | **No** |
| `unknown` | Evidence collection checklist | **No** |
| `agent` (reusable scientific/workflow gap) | Domain Skill prescription + Agent policy guidance | **Yes** |

## 3. Prescription Schema

When eligible, `skill_prescription` in `analysis.json` and `skill-prescription.md` must include:

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
