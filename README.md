<div align="center">

# Case Failure Analyzer

**Evidence-first causal failure analysis for scientific-computing & MLIP benchmark cases**

[English](README.md) · [简体中文](README.zh.md)

`Python 3 · stdlib-only runtime` &nbsp;|&nbsp; `4 peer categories` &nbsp;|&nbsp; `bilingual reports`

</div>

Works with `CP2K` · `Quantum ESPRESSO` · `VASP/ABACUS` · `ORCA/Gaussian/PySCF` · `LAMMPS` · `GROMACS/AMBER/OpenMM` · `MLIP (MACE/NequIP/DeePMD/CHGNet)` · `ASE` · `xTB` · `RDKit` · `SciPy/NumPy/JAX/PyTorch/FEniCS/OpenFOAM`.

---

## Contents

- [Highlights](#highlights)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Architecture](#architecture)
- [Repository Layout](#repository-layout)
- [Development](#development)
- [Documentation Index](#documentation-index)

---

## Highlights

| You get | What it looks like |
|---|---|
| **Errors at a glance** | An *Error Matrix*: failed check · kind · delivered vs expected · deviation / tolerance (× over bound) · source |
| **Readable evidence** | Opaque refs (`sci:…`, `sig:…`, `art:…`) resolved into plain-language items with file/line pointers |
| **Transparent confidence** | A score receipt (`points / denominator`), each `+`/`−` factor, and what is missing to raise it |
| **Four peer verdicts** | `agent` · `verifier` · `case` · `infra`, each marked primary / contributing / excluded with a reason |
| **Global board** | `INDEX.md` with pass rate, category shares (sum to 100% of non-passed), natural task ordering, direct links |

---

## Quick Start

```bash
# Audit one benchmark task by ID (picks the newest trial, prints the verdict card)
python3 scripts/cfa.py audit task208 --jobs-root <benchmark checkout> --lang zh

# Audit every task under a benchmark checkout
python3 scripts/cfa.py audit --all --jobs-root <benchmark checkout>

# Inspect results (read-only, credentials and home paths redacted)
python3 scripts/cfa.py inspect task208 --section errors
```

Reports land in `<benchmark>/failure-report/<task_id>/`, next to an auto-updated `INDEX.md`.

---

## CLI Reference

### `cfa.py` subcommands

| Command | Purpose |
|---|---|
| `audit <task>` / `audit --all` | Resolve trial(s), run the full pipeline, print the verdict card |
| `inspect <task> --section <name> [--raw]` | Read-only view of a finished audit |
| `index --report-root <dir>` | Atomically rebuild the global `INDEX.md` |

### `inspect` sections

| Section | Shows |
|---|---|
| `verdict` | The verdict card (`summary.md`) |
| `errors` | Verdict, root cause, verifier diagnosability, every named failed check with kind and deltas |
| `confidence` | Score, strength, `points / denominator`, signed factors, gap to raise confidence |
| `evidence` | Digest of runtime state, failed-check count, causal anchors, scientific observations, signals |
| `--raw` | Unformatted JSON for the chosen section |

### Path mode (`analyze_case.py`)

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --lang bilingual
```

| Option | Description |
|---|---|
| `--job` | Job directory or a specific trial directory **(required)** |
| `--output` | Output directory **(required)**; inputs are strictly read-only and paths must not overlap |
| `--task` | Task definition directory (auto-discovered if omitted) |
| `--trial` | Trial name or `all` (default `all`) |
| `--lang` | `bilingual` (default) · `zh` · `en` |
| `--format` | `both` (default) · `json` · `markdown` |
| `--phase` | `all` (pipeline + conservative attribution + reports) · `collect` (evidence + candidate hypotheses only) |

**Outputs** in `<output-dir>/<case_id>/`:

| File | Contents |
|---|---|
| `evidence.json` | Objective artifacts, `runtime_state`, normalized trajectory, contract audit, scientific error observations |
| `candidate-hypotheses.json` | Competing root-cause hypotheses with heuristic evidence scores |
| `analysis.json` | Causal attribution (`failure-analysis-v1`); a conservative deterministic draft under `--phase all` |
| `report.md` / `report.zh.md` / `report.en.md` | Audit report with 4 core sections (passed cases: lightweight view under 30 lines) |
| `skill-prescription*.md` | Domain skill prescription, only for reusable agent capability gaps |
| `job_summary.md` / `.json` | Multi-trial dashboard (relative identifiers, no machine-specific paths) |

**Evidence only:** add `--phase collect` to the command above.

**Model-in-the-loop:** validate a model-authored attribution, then render it:

```bash
python3 "$SKILL_DIR/scripts/validate_analysis.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --report   <output-dir>/<case_id>/report.md

python3 "$SKILL_DIR/scripts/render_report.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --output-report <output-dir>/<case_id>/report.md \
  --output-prescription <output-dir>/<case_id>/skill-prescription.md \
  --lang bilingual
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (including a legitimate job-level pre-startup failure) |
| `2` | Invalid input: missing path, unknown `--trial`, empty job tree without job-level evidence, or unimplemented `--replay` |

Validation fails fast **before** any report is written, so a bad path never yields a plausible-looking analysis.

---

## Architecture

```text
Artifact collectors      discover_artifacts · runtime_state · extract_runtime_errors
        ↓
Objective evidence       evidence.json
        ↓
Candidate hypotheses     generate_hypotheses → candidate-hypotheses.json
        ↓
Attribution engine       analyze_case + attribution-protocol → analysis.json
        ↓
Schema & invariants      validate_analysis
        ↓
Bilingual reports        render_report → report.md · report.zh.md · report.en.md · skill-prescription.md
```

The causal decision lives in `scripts/attribution/`: one function per gate, run in a fixed order by `engine.py`. The first match wins.

```text
gate0_passed → gate1a_infra_prestartup → gate1b_unknown_no_evidence
             → gate2_case_definition → gate3_verifier_defect
             → gate4_numerical_divergence → gate5_insufficient_positive_evidence
             → gate6_agent_primary
```

- Each gate either abstains or returns a complete 15-key attribution; `schema.py` enforces the key contract.
- Gate order is load-bearing and pinned by tests.
- `analysis.json` carries an optional `decision_trace` (which gates ran, why each abstained, which one was selected).
- `evidence.json` carries an optional `diagnostics` list; non-empty means a recoverable failure degraded the evidence (for example an unparseable `tests/verify.py`). Records are also printed to stderr as `ANALYSIS DIAGNOSTIC: {...}`; stdout and exit codes are unaffected.
- `tests/test_attribution_characterization.py` snapshots ten archetype fixtures against committed baselines. Regenerate deliberately with `UPDATE_BASELINE=1`.

---

## Repository Layout

```text
case-failure-analyzer/
├── SKILL.md                 # Skill entrypoint & runtime contract (agent-facing, English only)
├── README.md / README.zh.md # Project overview & CLI usage
├── Makefile                 # test, lint, smoke, check-docs, export-skill, clean
├── pyproject.toml           # Package metadata, optional [dev] extras
├── agents/openai.yaml       # codex runtime interface metadata
├── docs/{en,zh}/            # Human-readable mirrors of references/ (guarded by make check-docs)
├── references/              # Single source of truth for agent-consumed documents
│   ├── *.md                 # taxonomy, protocol, schemas, prescription policy
│   ├── error-families.json  # Unified error-family registry
│   └── software/            # 11 per-software knowledge bases
├── scripts/                 # Deterministic evidence pipeline & reporting
│   ├── analyze_case.py      # Entrypoint: collect → attribute → render
│   ├── cfa.py               # Unified CLI: audit / inspect / index
│   ├── staging.py           # Staging, file locking, input isolation, transactional publish
│   ├── validate_analysis.py # Schema & invariant validator
│   ├── render_report.py     # Report & prescription renderer
│   └── attribution/         # Attribution engine (engine, schema, context) + gates/gate0..gate6
└── tests/                   # unittest suite, fixtures, attribution baselines
```

Local artifacts (`.venv/`, `__pycache__/`, `*.egg-info/`, `.ruff_cache/`, `.dev/`) are excluded by `.gitignore`, as are benchmark-side `failure-report/` boards. The shipped skill contains only `SKILL.md`, `agents/`, `scripts/` and `references/`.

---

## Development

The runtime is stdlib-only; test tooling is not. `make test` needs no install.

| Command | Action |
|---|---|
| `make test` | `python3 -m unittest discover -s tests -t .` |
| `make check` | lint + tests + doc-mirror check + format check |
| `make compile` | Syntax check over `scripts/` and `tests/` |
| `make smoke` | End-to-end CLI run on the committed 2-trial fixture |
| `make dev` / `make test-pytest` | Optional: install `[dev]` extra / run pytest |
| `make export-skill [DEST=…]` | Export a clean skill tree |
| `make help` | List all targets |

### Exporting the production skill

`make export-skill` exports `SKILL.md`, `agents/`, `scripts/`, `references/` and excludes `tests/`, `docs/`, and dev configuration (`Makefile`, `pyproject.toml`, `uv.lock`, `.ruff_cache`, `.venv`, `.git`, `.dev/`, legacy `failure-analysis/`).

```bash
make export-skill                                  # default: $CODEX_HOME/skills/case-failure-analyzer
make export-skill DEST=/path/to/case-failure-analyzer
```

`scripts/export_skill.py` refuses unsafe destinations (`/`, `$HOME`, the repo root) and synchronizes non-destructively, preserving your own files and tracking managed ones in `.cfa_export_manifest.json`.

---

## Documentation Index

| Document | English | 简体中文 |
|---|---|---|
| Project Overview & CLI | [README.md](README.md) | [README.zh.md](README.zh.md) |
| System Guide & Workflow | [guide](docs/en/guide.md) | [guide](docs/zh/guide.md) |
| Failure Taxonomy (`failure-analysis-v1`) | [taxonomy](docs/en/taxonomy.md) | [taxonomy](docs/zh/taxonomy.md) |
| Causal Attribution Protocol | [protocol](docs/en/attribution-protocol.md) | [protocol](docs/zh/attribution-protocol.md) |
| Evidence & Analysis JSON Schemas | [schema](docs/en/evidence-schema.md) | [schema](docs/zh/evidence-schema.md) |
| Report Structure Specification | [report](docs/en/report-schema.md) | [report](docs/zh/report-schema.md) |
| Skill Prescription Policy | [policy](docs/en/skill-prescription-policy.md) | [policy](docs/zh/skill-prescription-policy.md) |

> `SKILL.md` and `references/` are maintained in pure English for direct consumption by the agent runtime.
