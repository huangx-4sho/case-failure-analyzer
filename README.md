# Case Failure Analyzer

English · [简体中文](README.zh.md)

CFA is a skill for analyzing failed scientific-computing benchmarks. It reads logs, agent trajectories, task instructions, verifier code, and deliverables, then attributes failures to agent decisions, verifier rules, task design, or infrastructure. Reports cite the evidence; insufficient evidence yields an unknown result.

![Analysis workflow: collect, cross-check, attribute, review](docs/images/cfa-workflow.en.svg)

Requires Python 3.9+ with no third-party runtime dependencies. Reports support Chinese, English, or both.

## Usage

From this repository root, export to Codex's user-level skills directory:

```bash
python3 scripts/export_skill.py --dest "$HOME/.agents/skills/case-failure-analyzer"
```

Then enter this prompt in a Codex chat:

```text
$case-failure-analyzer Analyze task208 in /path/to/benchmark.
Explain the primary cause and the evidence for all four categories in English,
and link the report.
```

Or run the CLI directly in a terminal:

```bash
python3 scripts/cfa.py audit task208 --jobs-root /path/to/benchmark --lang en
```

Replace the example path with a benchmark checkout containing `jobs/` and `tasks/`. A task number selects the most recently modified matching trial. See the [guide](docs/en/guide.md) for specific trials, batch audits, inspection, comparisons, and updates.

## Output

Task-ID mode writes to `<benchmark>/failure-report/<task_id>/` and returns an attribution summary in the terminal or chat. Follow this path to review the results:

![Report reading path: summary, full report, and evidence](docs/images/cfa-reports.en.svg)

## Documentation

[Installation and invocation](docs/en/guide.md#installation-and-invocation) · [Taxonomy](docs/en/taxonomy.md) · [Attribution rules](docs/en/attribution-protocol.md)

[Evidence fields](docs/en/evidence-schema.md) · [Report format](docs/en/report-schema.md) · [Skill prescriptions](docs/en/skill-prescription-policy.md) · [Skill definition](SKILL.md)
