# Report Schema (`report.md`)

**[English](../en/report-schema.md) | [简体中文](report-schema.md)**

Every generated `report.md` MUST include the following **4 core sections** in exact order with start-of-line headings (`## <N>. <Title>`) and non-empty body text so human reviewers and automated validators (`scripts/validate_analysis.py`) can audit the report consistently.

Following user and reviewer experience principles, reports adhere to the **Compact Attribute Table & Clean Focal Summary (紧凑属性表与聚焦高亮)** standard:
- **层级显式化**：在归因结论中，**首先以 Markdown 属性表明确评测结论、主因归属**（顶层大类 `agent` / `case` / `infra` / `verifier` ➔ 二级子类 `subtype` (`CODE`)）、阶段与置信度。
- **直击痛点**：紧接着以高亮引用卡片呈现【失败表现 (Failure manifestation)】与【归因判断 (Attribution)】：前者说明评测在哪里失败，后者说明失败由什么机制导致。
- **门控降噪**：隐藏正常启动下的 `agent_started=True, verifier_started=True` 底层门控标记，仅在发生启动前失败或容器异常中断时才在表下附带 Warning 门控警示。
- **路径去噪与分级收敛**：精简证据文件索引，使用统一基准目录相对路径锚点；当日志全部缺失时合并为单行概括；对缺失产物分级过滤，仅展示核心输入与计算产物，收敛内部临时/中间文件。
- **自然文本表述**：消除 Python 列表原生 `repr` 语法泄露，将支持/反驳证据转换为自然文本。
- **诊断透明化**：证据采集发生降级（`diagnostics` 中 `severity` 为 `warning` 或 `error`）时，报告必须在第 1 节给出醒目警示，并在第 2 节逐条列出降级诊断；不得让降级结果看起来与“证据干净”完全一致。

Every invocation of `scripts/render_report.py` / `scripts/analyze_case.py` generates:
- `report.md`: Compact Chinese-English bilingual report with parallel bilingual labels (4 core sections, no duplicated Part II English copy).
- `report.zh.md`: Clean standalone Chinese edition (4 core sections).
- `report.en.md`: Clean standalone English edition (4 core sections).

---

## Required Sections & Visual Structure

### 1. `## 1. 归因结论` (or `## 1. Root Cause Verdict`)
以紧凑 Markdown 属性表（`项目 (Field) | 结论 (Finding)`）配合高亮引用呈现：
- **评测结论 (Verdict)**：`failed` / `passed` / `errored`（包含执行状态、验证状态、reward）
- **主因 (Primary cause)**：【大类】➔ 【二级子类】(`CODE`)
- **阶段 (Stages)**：发生阶段 ➔ 检出阶段
- **置信度 (Confidence)**：数值、判定方式与证据强度
- **运行门控警示 (Gate Warning)**：仅在未正常启动或容器异常中断时附带警示块，正常启动时不展示冗余门控状态。
- **证据完整性警示 (Evidence Integrity Warning)**：仅当 `diagnostics` 中存在 `warning` / `error` 记录时出现，说明证据已降级及受影响结论，并指向第 2 节的明细。
- **高亮结论卡片**：
  - **失败表现 (Failure manifestation)**：验证器具名失败检查或数值偏差等直接失败现象。
  - **归因判断 (Attribution)**：大类 · 子类层面的因果机制（模型盲区、契约歧义、判分缺陷或环境故障）。

### 2. `## 2. 失败证据链` (or `## 2. Failure Evidence Chain`)
- **关键失败报错摘要**：提炼最核心的 1~3 行验证器错误日志。
- **首次不可恢复偏离点 (`first_unrecovered_deviation`)**：记录偏离事件 ref、时间戳与发生动作。
- **核心证据引用代号 (`evidence_refs`)**。
- **证据降级诊断 (Evidence Degradation Diagnostics)**：仅当存在 `warning` / `error` 诊断时出现，逐条列出 `code`、`stage`、`severity`、`message`、`impact` 与 `source_ref`；这些记录同样写入 `evidence.json` 并在 `job_summary.json` 中聚合计数。
- **证据链来源文件索引 (Evidence File Pointers)**：
  - **相对上下文锚点**：统一标明 `case`、`trial` 与 `task` 相对标识，消除长绝对路径折行与泄露噪音。
  - **核心日志与产物**：交互轨迹（`agent/trajectory.json`）、评测日志（`verifier/verify.log`）、任务规范（`instruction.md`）、计算产物（`results.json`）。全缺时单行合并概括。
  - **全量证据矩阵索引**：指引至 `evidence.json` 与 `analysis.json`。

### 3. `## 3. 假设检验与排除` (or `## 3. Hypothesis Audit`)
- **竞争假设仲裁**：美化为自然文本的候选假设支持证据与排除假设反驳理由，杜绝 Python 列表语法泄露。
- **伴随因素与异常信号**：次要伴随影响与行为信号条数统计（详细条目索引至 `evidence.json`）。

### 4. `## 4. 改进建议` (or `## 4. Recommended Actions`)
- **针对性整改行动 (Action Items)**：按责任主体（Case/Infra/Verifier/Agent）给出明确对策。
- **领域 Skill 处方**：针对智能体能力盲区推荐的领域协议技能。
- **核心缺失产物清单**：仅展示关键核心输入与计算产物，收敛内部临时/中间文件。

---

## Lightweight View for Passed Trials (通过案例精简版)

当评测判定为通过 (`verdict == "passed"`) 时，报告自动切换为轻量精简视图（严格预算控制在 30 行以内），仅保留通过裁定与验证证据，彻底移除失败根因、竞争失败假设、缺失产物清单与修复行动等无关噪音：

### 1. `## 1. 通过结论` (or `## 1. Verdict & Pass Summary`)
- **通过属性表**：评测结论（`passed` · reward）、阶段（执行与验证均通过）、置信度（`1.00` · 规则判定）。
- **通过依据**：无降级时标注评测规则全部通过，未检测到未恢复异常或规格违规。若存在 `warning` / `error` 证据降级诊断，则本节改述为“判定通过但证据采集降级”，并在通过依据之后输出证据完整性警示；逐条诊断仍放在第 2 节，不得继续声称“无任何偏离”。

### 2. `## 2. 验证证据` (or `## 2. Verification Evidence`)
- **任务与试次**：`task`, `trial`, `case` 相对标识，不输出任何机器绝对路径。
- **已验证产物记录**：仅展示实际存在的验证产物文件，不列出失败产物缺失项。
- **全量验证矩阵索引**：指引至 `evidence.json` 与 `analysis.json`（若使用 `--format markdown` 则展示对应说明）。

---

## Batch Summary (`job_summary.md` / `job_summary.json`)

Multi-trial jobs additionally emit `job_summary.md` (bilingual), `job_summary.zh.md`, `job_summary.en.md`, and `job_summary.json`.

- **`job_summary.md`**: overview table, root-cause category distribution, and a per-trial diagnostics table. When at least one trial has `warning` / `error` diagnostics, a batch-level evidence degradation notice is emitted above Section 1, and the per-trial table carries a `Degraded Evidence` column showing `⚠️ <warning+error count>` (or `-` for clean rows). A clean batch emits neither the notice nor a misleading zero row.
- **`job_summary.json`**:
  - `job_dir`: batch identifier (reports render only its basename).
  - `batch_statistics`: `total_trials`, `agent_started_count`, `verifier_started_count`, `scored_trial_count`, `not_scored_trial_count`, `scored_pass_rate`, `low_diagnosability_trial_count`, `verdict_counts`, `failure_stage_counts`, `category_counts`, `root_cause_code_counts`, `degraded_evidence_trial_count`, `diagnostic_severity_counts` (`{"warning": N, "error": N}`).
  - `trials[]`: per-trial summary rows with `trial_name`, `verdict`, `agent_started`, `verifier_started`, `failure_stage`, `detection_stage`, `category`, `code`, `confidence`, `diagnostic_counts`, `degraded_evidence`, `verifier_diagnosability` (`"normal"|"low"|null`). Collect-phase rows may also carry `phase: "collect"`.

`degraded_evidence_trial_count` counts trials whose evidence collection emitted at least one `warning` or `error` diagnostic; it is **not** a failure count and must not be folded into `verdict_counts`.

### 三态评分覆盖 (`### 1.1`)

`job_summary.md` 在第 1 节下新增附加的 `### 1.1` 子节，包含四行：**已评分通过 (Scored Passed)**、**已评分失败 (Scored Failed)**、**未评分 (Not Scored)**（Runner 在验证器启动前中止，`reward=None`）以及**已评分通过率 (Scored Pass Rate)**。其中的说明区分总体通过率（`passed / total_trials`，仍保留在第 1 节表中）与已评分通过率（`passed / scored_trials`）。当 `low_diagnosability_trial_count > 0` 时，批次级警示会提示部分验证器结论只是简短泛化的消息，既无具名检查也无数值偏差。该子节是附加的：`report.md` 的四个强制 `## <N>.` 小节与现有的第 1 节表均保持不变。

### 验证器报告区块（单 Trial `report.md`）

当 `evidence.verifier_report` 存在时，第 2 节会包含归一化后的验证器报告区块：`schema_family`、`primary_report`、带 layer/name/detail 的具名 `failed_checks`、`core_failures`、`failure_origin` 与 `summary`。若 `diagnosability == "low"`，该区块会明确说明验证器消息过于简短、无法定位失败检查，并列出 `diagnosability_reasons`，而不会把空的检查列表静默地当作完整证据呈现。

---

## 任务号快捷回答 (`--task-id task208`)

以任务引用调用本技能（例如 `/case failure analyzer task208`）时会启用任务号模式。`scripts/task_ref.py` 会把该引用解析为最新匹配的 trial、其 job 目录，以及包含 `task.toml` 的任务定义目录。`--jobs-root`（或 `$CFA_JOBS_ROOT`，随后依次为当前目录及其父目录）限定搜索范围；数字匹配为精确匹配，因此 `task208` 绝不会解析成 `task2080`。

**输出落点**：报告统一落到 `<bench_root>/failure-report/<task_id>/`。`bench_root` 由解析出的 trial 路径推导（`<bench>/<case>/jobs/<job>/<trial>` → `<bench>`；扁平 `<bench>/jobs/...` → `<bench>`）。bench 级目录下包含一个全局看板，以及每个已分析任务一个子目录：

```text
<bench_root>/failure-report/
├── INDEX.md                          # 全局看板：大类分布统计 + 各任务裁决链接
├── task207/
│   ├── task207.md                    # 主归因报告（干净 Markdown）
│   ├── task207.en.md                 # 仅 --lang bilingual 时生成的英文版
│   ├── summary.md                    # 根因速览卡片（与 stdout 完全一致）
│   ├── skill-prescription.md         # 仅在发现智能体能力缺口时生成
│   ├── data/
│   │   ├── evidence.json
│   │   ├── analysis.json
│   │   ├── summary.json
│   │   └── candidate-hypotheses.json
│   ├── trials/<trial_name>/          # 多试次轻量归档（仅保留结论）
│   └── .cfa_manifest.json
└── task208/
    └── ...
```

该任务目录位于所有已声明输入根（job 目录、任务定义、trial 目录）之外，`scripts/staging.py` 的隔离检查仍会拒绝与输入相同、位于输入内或作为输入父目录的输出。每个任务通过自己文件夹内的 `.cfa_manifest.json` 发布，因此重跑某一任务不会触碰其他任务的目录或用户自建文件。`--report-dir` 覆盖精确的落点文件夹，`--no-verdict-stdout` 则抑制打印出来的回答。`cfa index` 以原子方式重建 `INDEX.md`。

机器可读 JSON 全部下沉到 `data/`，顶层只保留干净的 Markdown。分层模式不会生成冗余的 `.zh.md` 副本（请求 `zh` 时只产出 `task207.md`）；`cfa inspect` 读取 `data/summary.json` 与 `data/evidence.json`。

| 文件 | 内容 |
|---|---|
| `task208.md`、`task208.en.md` | 完整因果审计报告，含 4 个强制 `## <N>.` 小节（双语模式额外产出英文版） |
| `summary.md` | 结论卡，与 stdout 逐字节一致 |
| `data/summary.json` | 机器可读的结论卡（`cfa-verdict-card-v1`） |
| `data/evidence.json`、`data/analysis.json`、`data/candidate-hypotheses.json` | 结构化证据 / 归因 / 候选假设 |
| `skill-prescription.md` | 仅在发现智能体能力缺口时生成 |
| `.cfa_manifest.json` | 任务目录内的发布清单，将清理范围限定在本任务文件内 |

路径模式（`--job` + `--output`）保留旧有的无前缀文件名（`report.md`、`evidence.json` 等）。

### 结论卡 (`summary.md` / stdout)

该卡**就是**直接回答；CLI 将其打印到 stdout，随后追加 `REPORT_DIR: <path>`，因此调用方无需打开任何文件即可作答。其顺序为刚性约束（承载结论语义）：

1. **主因与失败原因** —— 单行给出裁定、主因大类、`subtype`、`code`、置信度，以及具体的失败原因（具名失败检查 / 偏差数值）。内嵌的原始验证器 JSON 大块会被剥离。
2. **四大类裁决**（`## 1. 四大类裁决`）—— `agent`、`verifier`、`case`、`infra`，每一类都带状态（`主因` / `伴随因素` / `已排除` / `未选为主因` / `候选假设`）及有证据支撑的理由。`unknown` 是辅助归类桶，并非第五个并列大类。
3. **主因大类下的小分类**（`## 2. 主因大类下的小分类`）—— `subtype` → `code` → 置信度 → 依据，另附伴随/候选子类、具名失败检查、`evidence_refs` 与 `recommended_actions`。

当主因大类为 `unknown` 时，结论卡会直白说明证据无法定位出原因，并列出所缺失的内容；绝不臆造子类。结论卡采用中英双语标签，并遵循 `--lang`（默认中文，`--lang en` 时为英文）。
