# case failure analyzer

**[English](README.md) | [简体中文](README.zh.md)**

An evidence-first causal failure analysis skill and toolkit for scientific-computing and MLIP benchmark cases (`CP2K`, `Quantum ESPRESSO`, `VASP/ABACUS`, `ORCA/Gaussian/PySCF`, `LAMMPS`, `GROMACS/AMBER/OpenMM`, `MLIP (MACE/NequIP/DeePMD/CHGNet)`, `ASE`, `xTB`, `RDKit`, `SciPy/NumPy/JAX/PyTorch/FEniCS/OpenFOAM`).

---

## Documentation Index

| Document | English | 简体中文 |
|---|---|---|
| **Project Overview & CLI Usage** | [README.md](README.md) | [README.zh.md](README.zh.md) |
| **System Guide & Workflow Specification** | [docs/en/guide.md](docs/en/guide.md) | [docs/zh/guide.md](docs/zh/guide.md) |
| **Failure Taxonomy (`failure-analysis-v1`)** | [docs/en/taxonomy.md](docs/en/taxonomy.md) | [docs/zh/taxonomy.md](docs/zh/taxonomy.md) |
| **Causal Attribution Protocol** | [docs/en/attribution-protocol.md](docs/en/attribution-protocol.md) | [docs/zh/attribution-protocol.md](docs/zh/attribution-protocol.md) |
| **Evidence & Analysis JSON Schemas** | [docs/en/evidence-schema.md](docs/en/evidence-schema.md) | [docs/zh/evidence-schema.md](docs/zh/evidence-schema.md) |
| **Report Structure Specification** | [docs/en/report-schema.md](docs/en/report-schema.md) | [docs/zh/report-schema.md](docs/zh/report-schema.md) |
| **Skill Prescription Policy** | [docs/en/skill-prescription-policy.md](docs/en/skill-prescription-policy.md) | [docs/zh/skill-prescription-policy.md](docs/zh/skill-prescription-policy.md) |

> **Note on Agent Files**: `SKILL.md` and `references/` (`references/*.md`, `references/software/*.md`, `references/error-families.json`) are maintained in pure English for direct consumption by the Agent runtime.

---

## Architecture

```text
Artifact Collectors (discover_artifacts.py + runtime_state.py + extract_runtime_errors.py)
       ↓
Objective Evidence (evidence.json)
       ↓
Candidate Hypotheses (generate_hypotheses.py -> candidate-hypotheses.json)
       ↓
Causal Attribution Engine / Agent Protocol (analyze_case.py + attribution-protocol.md -> analysis.json)
       ↓
Schema & Invariant Validator (validate_analysis.py)
       ↓
Bilingual Reports (render_report.py -> report.md, report.zh.md, report.en.md, skill-prescription.md)
```

### Repository Layout

The git-tracked repository layout, with a one-line note per directory:

```text
case-failure-analyzer/
├── .gitignore                   # Local-artifact and build-cache exclusions
├── SKILL.md                     # Skill entrypoint & runtime contract (agent-facing, English only)
├── README.md / README.zh.md     # Project overview & CLI usage (bilingual EN | 简体中文)
├── Makefile                     # Engineering entrypoints: test, lint, smoke, check-docs, export-skill, clean
├── pyproject.toml               # Package metadata and optional [dev] extras
├── agents/
│   └── openai.yaml              # codex runtime interface metadata
├── docs/
│   ├── en/                      # Human-readable English mirror of references/ (guarded by make check-docs)
│   └── zh/                      # Human-readable Chinese mirror of references/
├── references/                  # Single source of truth for agent-consumed documents
│   ├── *.md                     # taxonomy, attribution protocol, evidence/report schemas, prescription policy
│   ├── error-families.json      # Unified error-family registry for scientific-computing & MLIP suites
│   └── software/                # 11 per-software knowledge bases (cp2k, vasp-abacus, lammps, mlip, ...)
├── scripts/                     # Deterministic evidence pipeline & reporting
│   ├── analyze_case.py          # CLI entrypoint: collect -> attribute -> render
│   ├── staging.py               # Staging lifecycle, advisory file locking, input isolation & transactional manifest publishing
│   ├── validate_analysis.py     # Schema & invariant validator (hard rules)
│   ├── render_report.py         # Bilingual report & skill-prescription renderer
│   ├── *.py                     # Evidence modules: artifact discovery, runtime state, trajectory
│   │                            #   normalization, contract audit, scientific errors, hypotheses
│   └── attribution/             # Causal attribution engine (engine.py, schema.py, context.py, ...)
│       └── gates/               # One gate per module, evaluated in fixed order (gate0..gate6)
└── tests/                       # unittest suite: python3 -m unittest discover -s tests -t .
    ├── fixtures/                # Committed fixtures: attribution baselines & regression job
    └── test_*.py                # Unit, decision-table & characterization tests
```

`.venv/`, `__pycache__/`, `*.egg-info/`, `.ruff_cache/` and `.dev/` are local build/run/IDE artifacts excluded by `.gitignore`. Benchmark-side `failure-report/` boards are generated outside the repo and are excluded as well; the shipped skill contains only `SKILL.md`, `agents/`, `scripts/` and `references/`.

---

## Usage

All commands use the skill-relative entrypoint. Running from inside the skill folder, the equivalent short form is `python3 scripts/analyze_case.py ...`.

### 1. Full End-to-End Analysis & Bilingual Report Generation

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --lang bilingual
```

#### CLI Parameters

- `--job`: Job directory (`jobs/<job_name>`) or specific trial directory (Required).
- `--output`: Output directory for evidence, analysis, and reports (Required; strictly read-only on inputs, paths cannot overlap).
- `--task`: Task definition directory path (Optional; auto-discovered if omitted).
- `--trial`: Specific trial name or `all` (default: `all`).
- `--lang`: Report language edition: `bilingual` (default), `zh` (standalone Chinese), or `en` (standalone English).
- `--format`: Output format: `both` (default), `json`, or `markdown`.
- `--phase`: Execution phase: `all` (default: full pipeline + conservative attribution + reports) or `collect` (evidence + candidate hypotheses only).

Outputs generated in `<output-dir>/<case_id>/`:

- `evidence.json`: Objective per-trial artifacts, unified `runtime_state`, normalized trajectory timeline, contract audit matrix, and scientific error observations.
- `candidate-hypotheses.json`: Competing root-cause hypotheses with heuristic evidence strength scores.
- `analysis.json`: Causal attribution (`failure-analysis-v1`) — a conservative deterministic draft under `--phase all`.
- `report.md`: Combined Chinese-English bilingual report covering all 4 high-signal core review sections (for passed cases: dedicated lightweight 2-section view < 30 lines without failure semantics).
- `report.zh.md`: Standalone Chinese report (lightweight 2-section view for passed cases).
- `report.en.md`: Standalone English report (lightweight 2-section view for passed cases).
- `skill-prescription.md` (plus `skill-prescription.zh.md` and `skill-prescription.en.md`): Bilingual domain skill prescription (generated **only** for reusable Agent capability gaps).
- `job_summary.md`: Multi-trial batch Markdown dashboard summarizing verdicts, categories, root causes, and failure stages across all trials (using relative identifiers, zero machine-specific path leaks).
- `job_summary.json`: Multi-trial batch structured statistics and summary records.

### 2. Evidence & Candidate Hypothesis Collection Only (`--phase collect`)

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --phase collect
```

### 3. Model-in-the-Loop Validation & Rendering

`--phase all` emits a conservative deterministic draft. To submit a model-authored attribution, validate then render it:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/validate_analysis.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --report  <output-dir>/<case_id>/report.md

python3 "$SKILL_DIR/scripts/render_report.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --output-report <output-dir>/<case_id>/report.md \
  --output-prescription <output-dir>/<case_id>/skill-prescription.md \
  --lang bilingual
```

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Analysis succeeded (including a legitimate job-level pre-startup failure). |
| `2` | Invalid input: missing `--job`/`--task` path, unknown `--trial`, empty job tree without job-level evidence, or an unimplemented `--replay` mode. |

The entrypoint fails fast **before** writing any report, so an invalid path can never yield a plausible-looking analysis.

---

## Running Tests

The runtime is stdlib-only; the test tooling is not. `make test` needs no install:

```bash
make test          # canonical: python3 -m unittest discover -s tests -t .
make compile       # syntax check over scripts/ and tests/
make smoke         # end-to-end CLI run against the committed 2-trial fixture
make export-skill  # export clean skill tree to DEST (default: $CODEX_HOME/skills/case-failure-analyzer)
make dev           # optional: install the [dev] extra (pytest)
make test-pytest   # optional pytest run
make help          # list all targets
```

Without `make`, the equivalent commands are:

```bash
python3 -m unittest discover -s tests -t .
python3 -m pytest -q          # only after `make dev`
```

---

## Exporting Production Skill

Export a clean, production-ready skill tree (`SKILL.md`, `agents/`, `scripts/`, `references/`) excluding test suites (`tests/`), local documentation mirrors (`docs/`), and developer build configurations (`Makefile`, `pyproject.toml`, `uv.lock`, `.ruff_cache`, `.venv`, `.git`, `.dev/`, legacy `failure-analysis/`, etc.):

```bash
# Export to default destination ($CODEX_HOME/skills/case-failure-analyzer or ~/.codex/skills/case-failure-analyzer)
make export-skill

# Or export to a custom destination
make export-skill DEST=/path/to/target/case-failure-analyzer
```

The export workflow is powered by `scripts/export_skill.py`, which enforces destination path safety (refusing `/`, `$HOME`, or the repository root) and non-destructive synchronization (preserving user custom files in the destination while tracking managed files in `.cfa_export_manifest.json`).

### Interactive diagnostics

`analysis.json` carries an optional `decision_trace`: the ordered list of gates that were evaluated, why each abstained, and which one was selected. It answers "why this root cause and not another" without reading the gate chain, and it is what `tests/test_attribution_decisions.py` asserts against.

`evidence.json` carries an optional `diagnostics` list. A non-empty list means some recoverable failure degraded the evidence — for example an unparseable `tests/verify.py`, which leaves the verifier looking clean because no AST-derived parser hazards could be collected. The same records are printed to stderr as one JSON line each (`ANALYSIS DIAGNOSTIC: {...}`); stdout and exit codes are unaffected.

### Attribution Architecture

The causal decision lives in `scripts/attribution/` as one function per gate, run in a fixed order by `engine.py`:

```text
gate0_passed → gate1a_infra_prestartup → gate1b_unknown_no_evidence
             → gate2_case_definition → gate3_verifier_defect
             → gate4_numerical_divergence → gate5_insufficient_positive_evidence
             → gate6_agent_primary
```

Each gate either abstains or returns a complete 15-key attribution, and the first match wins. Gate order is load-bearing and pinned by tests; `scripts/attribution/schema.py` enforces the key contract at construction.

`tests/test_attribution_characterization.py` snapshots the full `analysis` output for ten archetype fixtures against committed baselines, so any change to attribution behavior shows up as a reviewable diff rather than as a subtly different report. Regenerate deliberately with `UPDATE_BASELINE=1`.

