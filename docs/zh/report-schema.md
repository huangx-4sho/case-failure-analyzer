# 报告规范（`report.md`）

**[English](../en/report-schema.md) | [简体中文](report-schema.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="report-schema-reportmd"></a>

**本页：** [核心章节与呈现方式](#核心章节与呈现方式) · [通过试次的精简视图](#通过试次的精简视图) · [批次汇总](#批次汇总job_summarymd--job_summaryjson) · [任务号报告](#任务号快捷回答---task-id-task208)

报告开头的 `causal_assessment.level` 区分已观察到的失败、规则选出的候选原因，以及证据审查支持的原因。单项数值偏差或启发式分数只能提供线索。交付物表分别标明已采集、采集目录中未找到、采集失败或无法判定。验证器检查过哪些内容、还有哪些缺口，另有记录。[已有证据审查](evidence-review.md)

失败报告按下面的顺序包含四个核心章节。标题以 `## <N>. <Title>` 顶格书写，各节正文不能为空，供人工和 `scripts/validate_analysis.py` 核验。通过试次采用后文规定的两节精简视图。

`report.md` 是按 `--lang` 选择的主报告，不代表英文版。显式目录模式使用 `--lang bilingual` 才会同时生成独立的 `report.zh.md` 与 `report.en.md`；`--lang zh` 的 `report.zh.md` 是中文别名。任务号模式对应的英文版为 `<task_id>.en.md`。生成的标题与说明遵守所选语言，未翻译的来源文字明确标为原文引用；审阅者可提供 `summary_en` / `summary_zh` 等字段。

评分覆盖按实际产出分数统计，而不是按验证器启动次数统计。报告保留原始命名指标和运行异常；评分契约复核、重放范围和科学定义差异在证据节分别展示。

验证器要求语义复核且尚未产分时，报告标为 `pending`，可按保存的评审条件继续核对。错因未隔离时，查看证据净分及按下限记 0 的说明。契约存在分歧时，规则也可能暂缓归因并记 0。已经复核的报告保留原规则分，并单列复核结论。

<a id="required-sections--visual-structure"></a>

## 核心章节与呈现方式

第 1 节用紧凑的 Markdown 属性表呈现实测裁决、主因类别 → 子类（`CODE`）、阶段与置信度，随后分别列出失败表现与归因机制。两者回答不同问题：前者说明验证在哪里失败，后者说明原因判断。

证据索引使用相对路径，采用自然语言归纳，不直接输出 Python 列表语法。若所有日志缺失，用一行合并说明。只展示核心缺失输入与计算产物，略去无关的临时中间文件。省略正常的 `agent_started=True, verifier_started=True` 标志；仅在启动失败或容器异常中断时显示门控警示。

评分明细放在可展开区域，主表保留分值、评估方式与证据强度。偏离点与假设条目中省略主因解释的重复文本，仅保留事件标识、支持证据和不同观点。JSON 数据链接按实际输出布局生成。缺失文件和运行器结果记录不应写成智能体提交的计算产物。

若 `diagnostics` 包含 `warning` 或 `error`，证据降级必须清晰可见：第 1 节说明受影响的结论，第 2 节逐条列出诊断详情。

显式目录模式按语言与格式选项生成报告文件：

| 文件 | 说明 |
| --- | --- |
| `report.md` | 所选语言的主报告。双语模式采用对照标签，并另写一份英文版 |
| `report.zh.md` | 独立中文版 |
| `report.en.md` | 独立英文版 |

<a id="1--1-归因结论-or--1-root-cause-verdict"></a>

### 1. 归因结论

报告标题：`## 1. 归因结论` 或 `## 1. Root Cause Verdict`。

| 项目 | 内容要求 |
| --- | --- |
| 裁决（Verdict） | `failed`、`passed`、`pending` 或 `errored`，含执行状态、验证状态与 `reward` |
| 主因（Primary cause） | 类别 → 子类（`CODE`） |
| 阶段（Stages） | 发生阶段 → 检出阶段 |
| 置信度（Confidence） | 分值、评估方式与证据强度 |
| 运行门控警示 | 仅在启动失败或容器异常中断时出现 |
| 证据完整性警示 | 仅当 `diagnostics` 含 `warning` / `error` 时出现，说明影响并指向第 2 节 |
| 失败表现（Failure manifestation） | 验证器具名失败检查或数值偏差 |
| 归因判断（Attribution） | 大类 · 子类层面的因果机制：智能体能力缺口、契约歧义、评分缺陷或环境故障 |

<a id="2--2-失败证据链-or--2-failure-evidence-chain"></a>

### 2. 失败证据链

报告标题：`## 2. 失败证据链` 或 `## 2. Failure Evidence Chain`。

- 提炼最关键的 1~3 行验证器报错摘要。
- 记录 `first_unrecovered_deviation` 的事件引用、时间戳与动作，以及核心 `evidence_refs`。
- 若存在证据降级诊断，列出 `code`、`stage`、`severity`、`message`、`impact` 与 `source_ref`；这些记录同样保存在 `evidence.json` 并计入 `job_summary.json`。
- 用相对标识指明 `case`、`trial` 与 `task`，避免输出冗长的本机绝对路径。索引核心材料：`agent/trajectory.json`、`verifier/verify.log`、`instruction.md` 和 `results.json`。全缺时合并成一行概括。
- 链接完整的 `evidence.json` 与 `analysis.json`。

<a id="3--3-假设检验与排除-or--3-hypothesis-audit"></a>

### 3. 假设检验与排除

报告标题：`## 3. 假设检验与排除` 或 `## 3. Hypothesis Audit`。

用自然语言列出候选假设的支持证据与排除理由，不使用 Python 列表语法。附上伴随因素与行为信号计数；逐条明细索引至 `evidence.json`。

<a id="4--4-改进建议-or--4-recommended-actions"></a>

### 4. 改进建议

报告标题：`## 4. 改进建议` 或 `## 4. Recommended Actions`。

按责任方给出可落地的处理动作，并说明仍缺哪些输入或输出才能进一步定因。单次试次可以提出改进建议；确认可复用的指导需要另外对比多个独立任务。

<a id="lightweight-view-for-passed-trials-通过案例精简版"></a>

## 通过试次的精简视图

当 `verdict == "passed"` 时，报告切换为 30 行以内的精简视图。仅保留通过裁定与验证证据，省略失败原因、竞争假设、缺失产物清单和整改建议。

<a id="1--1-通过结论-or--1-verdict--pass-summary"></a>

### 1. 通过结论

报告标题：`## 1. 通过结论` 或 `## 1. Verdict & Pass Summary`。

显示 `passed` 与 `reward`、执行与验证阶段、以及规则置信度 `1.00`。无降级时，说明验证全部通过且未检测到未恢复异常或契约违约。存在 `warning` / `error` 诊断时，说明验证虽通过但取证降级，附完整性警示并将明细列在第 2 节，不再声称无任何偏离。

<a id="2--2-验证证据-or--2-verification-evidence"></a>

### 2. 验证证据

报告标题：`## 2. 验证证据` 或 `## 2. Verification Evidence`。

展示相对 `task`、`trial`、`case` 标识，不输出本机绝对路径。仅列出实际存在的验证产物，不列缺失产物表。链接 `evidence.json` 与 `analysis.json`（使用 `--format markdown` 时说明未生成的原因）。

<a id="batch-summary-job_summarymd--job_summaryjson"></a>

## 批次汇总（`job_summary.md` / `job_summary.json`）

多试次作业还会生成 `job_summary.md`（双语）、`job_summary.zh.md`、`job_summary.en.md` 和 `job_summary.json`。

- **`job_summary.md`**：总览、根因类别分布和逐试次诊断表。任一试次有 `warning` / `error` 诊断时，在第 1 节之前显示批次证据降级提示；逐试次表增加 `Degraded Evidence` 列，显示 `⚠️ <warning+error count>`，无降级的行显示 `-`。没有降级时，不显示该提示或无意义的零值行。
- **`job_summary.json`**:
  - `job_dir`：批次标识，报告只显示目录名。
  - `batch_statistics`: `total_trials`, `agent_started_count`, `verifier_started_count`, `scored_trial_count`, `not_scored_trial_count`, `scored_pass_rate`, `low_diagnosability_trial_count`, `verdict_counts`, `failure_stage_counts`, `category_counts`, `root_cause_code_counts`, `degraded_evidence_trial_count`, `diagnostic_severity_counts` (`{"warning": N, "error": N}`).
  - `trials[]`：逐试次摘要，包含 `trial_name`, `verdict`, `agent_started`, `verifier_started`, `failure_stage`, `detection_stage`, `category`, `code`, `confidence`, `diagnostic_counts`, `degraded_evidence`, `verifier_diagnosability` (`"normal"|"low"|null`). 仅取证阶段的记录还可能包含 `phase: "collect"`。

`degraded_evidence_trial_count` 统计证据采集至少出现一条 `warning` 或 `error` 的试次数量。它不等于失败数量，不得并入 `verdict_counts`。

### 评分覆盖 (`### 1.1`)

`job_summary.md` 在第 1.1 节列出已评分通过、已评分失败、待独立复核以及未评分试次，存在提交检查未通过记录时增加相应行。总体通过率为 `passed / total_trials`，已评分通过率为 `passed / scored_trials`。当 `low_diagnosability_trial_count > 0` 时，报告会标出缺少具名检查或数值细节的结论。

### 验证器报告区块（单 Trial `report.md`）

当 `evidence.verifier_report` 存在时，第 2 节会包含归一化后的验证器报告区块：`schema_family`、`primary_report`、带 layer/name/detail 的具名 `failed_checks`、`core_failures`、`failure_origin` 与 `summary`。若 `diagnosability == "low"`，该区块会明确说明验证器消息过于简短、无法定位失败检查，并列出 `diagnosability_reasons`，而不会把空的检查列表静默地当作完整证据呈现。

## 任务号快捷回答 (`--task-id task208`)

以任务引用调用本技能（例如 `$case-failure-analyzer task208`）时会启用任务号模式。`scripts/task_ref.py` 会把该引用解析为最新匹配的 trial、其 job 目录，以及包含 `task.toml` 的任务定义目录。`--jobs-root`（或 `$CFA_JOBS_ROOT`，随后依次为当前目录及其父目录）限定搜索范围；数字匹配为精确匹配，因此 `task208` 不会解析成 `task2080`。

**输出落点**：报告统一落到 `<bench_root>/failure-report/<job>/<task_id>/`。`bench_root` 由解析出的 trial 路径推导（`<bench>/<case>/jobs/<job>/<trial>` → `<bench>`；扁平 `<bench>/jobs/...` → `<bench>`）。bench 级目录下包含一个全局看板，各 job 各有一个目录，目录内按任务编号存放报告：

```text
<bench_root>/failure-report/
├── INDEX.md                          # 全局看板：三态评测统计、占未通过比例归一化、自然排序、置信度指标与直达链接
└── <job>/
    ├── INDEX.md
    ├── task207/
    │   ├── task207.md                    # 主归因报告（干净 Markdown）
    │   ├── task207.en.md                 # 仅 --lang bilingual 时生成的英文版
    │   ├── summary.md                    # 根因速览卡片（与 stdout 完全一致）
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

机器可读 JSON 全部下沉到 `data/`，顶层只保留干净的 Markdown。分层模式不会生成冗余的 `.zh.md` 副本（请求 `zh` 时只产出 `task207.md`）；`cfa inspect` 读取 `data/summary.json`、`data/evidence.json` 与 `data/analysis.json`。

| 文件 | 内容 |
|---|---|
| `task208.md`、`task208.en.md` | 完整因果审计报告，含 4 个强制 `## <N>.` 小节（双语模式额外产出英文版） |
| `summary.md` | 结论卡，与 stdout 逐字节一致 |
| `data/summary.json` | 机器可读的结论卡（`cfa-verdict-card-v1`） |
| `data/evidence.json`、`data/analysis.json`、`data/candidate-hypotheses.json` | 结构化证据 / 归因 / 候选假设 |
| `.cfa_manifest.json` | 任务目录内的发布清单，将清理范围限定在本任务文件内 |

路径模式（`--job` + `--output`）保留旧有的无前缀文件名（`report.md`、`evidence.json` 等）。

### 全局看板 (`INDEX.md`)

基准级全局看板汇总全量已分析任务的归因结果：

- **试次统计**：统计总用例数、已评分通过、已评分失败、待独立复核、未评分或异常数，以及已评分通过率。
- **类别分布**：展示已分类未通过试次在四类根因与 `unknown` 桶中的占比；已通过与待复核试次不计入该分布分母。
- **自然数字排序**：任务清单按任务编号自然排序（`task98` 在 `task194` 和 `task207` 之前），避免按字符序排乱。
- **置信度视觉标识**：`🟢` 表示 `>= 0.65`，`🟡` 表示 `0.40..0.64`，`🔴` 表示 `< 0.40`，通过试次显示 `✅ 1.00`。
- **直达链接**：提供 `[速览]`（`summary.md`）、`[报告]`（`{task_id}.md`）和 `[数据]`（`data/`）链接；历史报告可能保留 `skill-prescription.md`，新的跨任务指导保存在独立复核结果中。

### 一目了然错误矩阵与证据摘要检视

`scripts/cfa.py inspect` 提供聚焦、只读的检视功能，默认自动脱敏凭据与家目录路径：

- **一目了然错误矩阵** (`cfa inspect <task_id> --section errors`)：以终端友好的 Markdown 表格呈现裁决、主因大类、验证器可诊断性及每一项具名失败检查（`[<layer>] <name>`、`failure_kind`、`detail`，以及产物对比或复算散度数值）。传入 `--raw` 输出原始 JSON。
- **置信度收据与因子明细** (`cfa inspect <task_id> --section confidence`)：展示启发式置信度、证据强度、动态分值标尺（`points / denominator`）、带正负符号的证据因子明细、提升置信度所需补充证据，以及竞争假设得分。传入 `--raw` 输出结构化收据 JSON。
- **规整化证据摘要** (`cfa inspect <task_id> --section evidence`)：输出紧凑的运行状态摘要、失败检查计数、因果锚点、科学观测、行为信号计数及核心证据引用，无需展开完整 JSON。传入 `--raw` 输出原始 `evidence.json`。
- **全量用例自动发现与批量审计** (`cfa audit --all --jobs-root <bench_root>`)：自动扫描基准仓库（支持 Harbor 多套件布局与扁平布局），在各 job 内自然排序并审计每个任务的最新试次，随后更新 `<bench_root>/failure-report/INDEX.md`。

### 结论卡 (`summary.md` / stdout)

该卡就是直接回答；CLI 将其打印到 stdout，随后输出 `REPORT_DIR: <path>`，调用方无需打开文件即可作答。输出顺序固定：

1. **结论等级与失败原因**：给出评测结论、候选或复核后的类别、`subtype`、`code`、启发式置信度，以及具体的运行中断或被拒检查项。
2. **四大类裁决**：并列给出 `agent`、`verifier`、`case`、`infra` 的状态与依据；未被规则选中的替代假设保持未定或排除理由。
3. **选中大类下的子类**：列出子类、代码、置信度、依据、伴随因素、证据与改进建议；主因指向 `Agent` 时附上单试次能力诊断。
4. **报告链接**：使用输出的 `REPORT_DIR` 及所请求语言版本对应的文件名。

当主因类别为 `unknown` 时，结论卡说明仍需哪些证据才能锁定原因，不猜测子类；已通过或待复核试次使用对应的简短回答。结论卡使用所选报告语言（`--lang zh` 或 `--lang en`）。
