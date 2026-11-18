# 证据与归因字段（`failure-analysis-v1`）

**[English](../en/evidence-schema.md) | [简体中文](evidence-schema.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="证据与归因-json-模式规范-failure-analysis-v1"></a>

**本页：** [证据字段](#1-evidencejson-客观证据模式) · [归因字段](#2-analysisjson-因果归因模式) · [Harbor 扩展](#3-harbor-协议扩展) · [核对与溯源](#4-数值核对与溯源扩展) · [证据审查](#5-已有证据审查字段) · [评分契约](#6-评分契约与运行阶段复核)

本文说明证据、归因及扩展字段。示例可按需展开；标识、字段名和枚举值保持代码中的写法。

| 数据 | 用途 |
| --- | --- |
| `evidence.json` | 运行状态、轨迹、检查结果和证据位置 |
| `analysis.json` | 主根因、候选假设、行动建议和判定记录 |
| 可选扩展字段 | Harbor 采集信息、数值核对与溯源线索 |

## 1. `evidence.json` 客观证据模式

`evidence.json` 仅存储由确定性脚本（`discover_artifacts.py`、`runtime_state.py`、`normalize_trajectory.py`、`extract_runtime_errors.py`、`audit_contract.py`、`extract_scientific_errors.py`）提取的客观事实与观测，绝不包含任何主观根因裁决标签。

<details>
<summary>展开 evidence.json 字段示例</summary>

```json
{
  "case_id": "string (例如 ccb-cp2k-aimd-water 或 trial_name)",
  "job_dir": "string",
  "task_dir": "string 或 null",
  "trial_name": "string",
  "trial_dir": "string 或 null",
  "artifacts": [
    {
      "artifact_id": "art:trial_result",
      "rel_path": "result.json",
      "scope": "trial|job|task",
      "exists": true,
      "size_bytes": 3869,
      "sha256": "sha256:..."
    }
  ],
  "runtime": {
    "started_at": "ISO-8601 或 null",
    "finished_at": "ISO-8601 或 null",
    "execution_status": "completed|errored|not_started|unknown",
    "verification_status": "passed|failed|pending|errored|not_run",
    "verdict": "passed|failed|pending|errored|unknown",
    "exit_status": "completed|failed|errored|unknown",
    "reward": 0.0,
    "agent_started": true,
    "verifier_started": true,
    "state_conflicts": [],
    "stages": {
      "environment_setup": {"started_at": "...", "finished_at": "..."},
      "agent_setup": {"started_at": "...", "finished_at": "..."},
      "agent_execution": {"started_at": "...", "finished_at": "..."},
      "verifier": {"started_at": "...", "finished_at": "..."}
    }
  },
  "metadata": {
    "job_stats": {"n_total_trials": 2, "n_errored_trials": 1},
    "job_result_summary": {"n_total_trials": 2, "n_errored_trials": 1},
    "agent_info": "object 或 null（runner 提供的原始 agent 元数据）",
    "task_toml": "object 或 null（解析后的 task.toml）"
  },
  "timeline": [
    {
      "event_id": "trajectory:step:1",
      "timestamp": "2026-09-19T05:45:50.652Z",
      "actor": "user|agent|runner|verifier",
      "event_type": "user_instruction|agent_reasoning|tool_call|tool_output|shell_error|file_read|file_write|repeated_command|final_response|verifier_result|runtime_exception",
      "tool_name": "Bash|Read|Write|Edit|null",
      "command": "string 或 null",
      "observation": "截断后的观测字符串 或 null",
      "observation_sha256": "sha256:... 或 null",
      "exit_code": 0,
      "source_file": "agent/trajectory.json",
      "source_pointer": "/steps/0"
    }
  ],
  "behavioral_signals": [
    {
      "signal_id": "sig:repeated_fail:2:0",
      "signal_type": "repeated_failed_action|ignored_error|missing_log_inspection|unverified_output|premature_completion|wrong_output_path|asset_modified|dependency_search_attempted|scientific_parameter_changed|fallback_attempted",
      "description": "客观行为信号描述",
      "event_ref": "trajectory:step:2:tool:0"
    }
  ],
  "error_observations": [
    {
      "error_id": "err:1",
      "code": "INFRA_CONTAINER_BUILD",
      "subtype": "container_build",
      "stage": "environment_build",
      "matched_text": "匹配的原始错误文本",
      "causal_candidate": true,
      "transient": false,
      "source_file": "result.json",
      "source_pointer": "/exception_info/exception_message"
    }
  ],
  "contract_observations": [
    {
      "contract_id": "contract:1",
      "item": "results.json",
      "prompt_requirement": "required",
      "verifier_requirement": "required",
      "agent_output_status": "present",
      "alignment": "consistent|implicit_consistent|agent_missing_output|agent_schema_mismatch|verifier_hidden_requirement|verifier_schema_mismatch|verifier_defect|case_defect",
      "details": "三方契约比对详情",
      "source_refs": ["task:instruction.md", "task:tests/verify.py"]
    }
  ],
  "verifier_observations": [
    {
      "obs_id": "ver:hazard:1",
      "type": "verifier_fail_message|parser_hazard|implicit_column_assumption|verifier_internal_crash",
      "summary": "评测器观测摘要",
      "matched_text": "匹配文本",
      "hazard_detected": true,
      "failure_binding": "direct|indirect|none",
      "binding_evidence": ["ver:fail_log"],
      "affected_input": "受影响输入文件 或 null",
      "affected_parser": "受影响解析函数 或 null",
      "triggered": true,
      "source_file": "tests/verify.py",
      "source_pointer": "AST"
    }
  ],
  "scientific_observations": [
    {
      "sci_id": "sci:1",
      "software": "cp2k|quantum-espresso|lammps|xtb|ase|rdkit|vasp|gaussian|orca|gromacs|pyscf",
      "error_family": "scf_nonconvergence",
      "aliases": [],
      "reference_anchor": "references/software/cp2k.md#scf_nonconvergence",
      "matched_text": "科学软件报错匹配片段",
      "source_ref": "trajectory:step:8",
      "candidate_causes": ["bad_initial_guess", "insufficient_scf_iterations"],
      "required_discriminating_evidence": ["SCF iteration history", "MAX_SCF setting"]
    }
  ],
  "diagnostics": [
    {
      "diagnostic_id": "diag:verifier_check_stats:VERIFIER_CHECK_STATS_BUILD_FAILED",
      "schema_version": "diagnostics-v1",
      "stage": "verifier_check_stats",
      "code": "VERIFIER_CHECK_STATS_BUILD_FAILED",
      "severity": "info|warning|error",
      "message": "Verifier check statistics could not be built: ValueError",
      "source_ref": "verifier/verify.log 或 null",
      "error_type": "ValueError 或 null",
      "error_message": "string 或 null",
      "impact": "该降级导致分析器无法继续得出的结论",
      "context": {"max_log_bytes": 120000}
    }
  ],
  "verifier_check_stats": {
    "total_checks": 2,
    "passed_checks": 1,
    "failed_checks": 1,
    "failed_check_summaries": ["reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)"],
    "single_failure": true,
    "widespread_failure": false,
    "counterfactual_candidate": true
  },
  "missing_artifacts": [
    "agent/trajectory.json"
  ]
}
```

</details>

**证据完整性字段：**

- `trial_dir` / `task_dir` 为采集阶段解析出的绝对输入路径；报告只渲染相对锚点，绝不暴露这些绝对路径。
- `metadata` 保留原始 runner 元数据（`job_stats`、`agent_info`、解析后的 `task.toml`）供审计，不作为证据解释。
- `diagnostics` 为结构化恢复记录列表（`diagnostics-v1`）。`severity="warning"` 表示证据已降级，结论可能建立在比表面更弱的基础上；`severity="error"` 表示必需输入无法读取。`warning` / `error` 记录会在 `report.md` 第 1 节警示，并在第 2 节逐条列出；汇总行与 `job_summary.json` 会聚合其计数。
- `verifier_check_stats` 为**可选字段**，仅当存在 verifier 日志时出现。对于 fail-fast 验证器，`total_checks` 是下界（其通过的检查不打印标记）。只有 `single_failure` 可视为检查定位（localization）；`counterfactual_candidate`（恰好一个失败检查）只是*候选*，无法区分“唯一失败的检查”与“唯一运行过的检查”，门控必须将其与指标级证据独立绑定后，才能给予反事实置信度。

## 2. `analysis.json` 因果归因模式

`analysis.json` 存储结构化因果归因结论，必须通过 `scripts/validate_analysis.py` 校验。

<details>
<summary>展开 analysis.json 字段示例</summary>

```json
{
  "schema_version": "failure-analysis-v1",
  "case_id": "cp2k-aimd-water",
  "trial_name": "cp2k-aimd-water__kGRrYBg",
  "verdict": "failed|errored|passed",
  "failure_stage": "environment_build|environment_runtime|agent_setup|agent_execution|verifier_execution|none|unknown",
  "detection_stage": "runner|verifier_execution|agent_execution",
  "first_unrecovered_deviation": {
    "status": "identified|not_identified",
    "event_ref": "err:1 或 null",
    "timestamp": "2026-09-18T21:45:37.672525",
    "summary": "在 Agent 启动前的环境准备阶段发生 Docker 镜像拉取/构建失败"
  },
  "failure_manifestation": {
    "type": "dependency_install_failure|verifier_check_failed|verifier_internal_crash|...",
    "summary": "最终失败表象的可读摘要说明"
  },
  "primary_root_cause": {
    "category": "infra|case|agent|verifier|unknown|none",
    "subtype": "external_network",
    "code": "INFRA_EXTERNAL_NETWORK",
    "confidence": 0.86,
    "confidence_kind": "heuristic_evidence_score",
    "evidence_strength": "high|medium|low",
    "summary": "为何该项是唯一主根因的因果解释"
  },
  "contributing_factors": [],
  "competing_hypotheses": [
    {
      "hypothesis_id": "H1",
      "category": "infra",
      "subtype": "external_network",
      "claim": "Agent 启动前因网络超时/不可达导致 Docker 构建失败",
      "evidence_for": ["err:1"],
      "evidence_against": [],
      "missing_evidence": [],
      "counterfactual_test": "预先拉取基础镜像或配置可用镜像源后重新构建",
      "confidence": 0.86,
      "confidence_kind": "heuristic_evidence_score",
      "evidence_strength": "high"
    }
  ],
  "evidence_refs": ["err:1", "art:trial_result"],
  "excluded_hypotheses": [
    {
      "hypothesis_id": "H2",
      "category": "agent",
      "reason": "Agent 根本未启动 (runtime.agent_started == false)；轨迹缺失是启动前构建失败的结果而非原因。"
    }
  ],
  "recommended_actions": [
    {
      "owner": "Infra|Case|Verifier|Agent Policy",
      "action": "具体的修复行动建议"
    }
  ],
  "skill_prescription": null,
  "decision_trace": {
    "schema_version": "decision-trace-v1",
    "engine": "attribution-engine-v1",
    "gate_order": ["gate0_passed", "gate1a_infra_prestartup", "..."],
    "evaluated": [
      {
        "gate_id": "gate0_passed",
        "matched": false,
        "reason": "trial did not pass verification",
        "checks": {"verification_status": "not_run", "reward": null}
      }
    ],
    "selected": {
      "gate_id": "gate1b_unknown_no_evidence",
      "category": "unknown",
      "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
      "verdict": "errored",
      "failure_stage": "unknown"
    },
    "reconciled_competing_hypotheses": true
  }
}
```

</details>

`decision_trace` 为附加审计字段：记录每个被评估的门控、其未命中的原因以及最终选中的门控。`checks` 仅保存标量（不含日志文本），且不记录任何时间相关值，因此 `analysis.json` 保持字节级确定性。仅当门控选中已文档化的子情形时才出现 `subcase_id`。

<a id="3-harbor-协议扩展附加式可选键harbor-protocol-extensions"></a>

## 3. Harbor 协议扩展

Harbor 作业（`jobs/<timestamp>/<trial>/`，含 `result.json`、`agent/trajectory.json`、`verifier/`、`artifacts/` 以及同级的 `task.toml`）会额外产出若干观测键。这些键全部为**可选**：非 Harbor 输入仍保持旧有结构，校验器也不强制要求它们。

<details>
<summary>展开 Harbor 扩展示例</summary>

```json
{
  "metadata": {
    "task_toml": {"artifacts": ["/app/final", "/app/results.json"]},
    "declared_deliverables": ["/app/final", "/app/results.json"]
  },
  "missing_deliverables": ["/app/final"],
  "verifier_report": {
    "schema_family": "envelope|envelope+layered|checks|criteria",
    "primary_report": "verify.json",
    "source_files": [{"rel_path": "core/verify.json", "role": "nested"}],
    "verdict": "fail",
    "scorable": true,
    "reward": 0.0,
    "status": "SCORED",
    "scientific_status": "FAIL",
    "failure_origin": {"role": "candidate", "reason_code": "SCIENTIFIC_CRITERIA_FAILED"},
    "core_failures": ["coordination_distances_match_derived_reference"],
    "messages": [],
    "checks": [{"layer": "L3", "name": "...", "ok": false, "detail": "...", "core": false, "source": "core/verify.json"}],
    "failed_checks": [{"layer": "L3", "name": "...", "ok": false, "detail": "...", "core": false, "source": "core/verify.json"}],
    "failed_check_summaries": ["[L3] ..."],
    "diagnosability": "normal|low",
    "diagnosability_reasons": [],
    "summary": "[L3] ..."
  }
}
```

</details>

- **`metadata.task_toml.artifacts`** 是权威的已声明交付物集合，`metadata.declared_deliverables` 将其镜像为扁平列表。`normalize_trajectory(..., expected_deliverables=[...])` 用它来判定 `sig:premature_completion` 是否真实成立：仅当某个*已声明*路径在轨迹中缺失时该信号才触发，绝不会因为硬编码的 `results.json` 未写出而触发。当 `task.toml` 不可用时该列表为空，回退到旧有的 `results.json` 启发式。
- **`missing_deliverables`** 是从未产出的已声明路径的对账（reconciled）列表。轨迹匹配本身是启发式的，因此 `collect_case_evidence.py` 会交叉核对 `<trial>/artifacts/app/<已声明路径>`（Harbor 的 `docker cp` 输出，无法凭空想象），并剔除任何实际已被采集到的路径。`premature_completion` 信号会携带 `declared_deliverables` / `missing_deliverables` 两个数组；当所有已声明路径都已被采集时，该信号会被整体移除。
- **`verifier_report`** 将五类 Harbor 验证器 JSON 家族（分层式 `core/verify.json`、带 `ACADEMIC_INTEGRITY_VETO` 的 `scientific/verify.json`、`raw-verifier.json` 的 `criteria[]`、`science.json` 的 `checks[]`、以及带 `failure_origin` 的 `verify.json` 信封）归一化为统一形态：带 `layer`、`name`、`detail`、`core`、`source` 的具名 `failed_checks`；`core_failures`；`failure_origin`；`scorable`；`reward`；以及 `diagnosability` 标记。`diagnosability == "low"` 表示验证器只给出一条简短、泛化的消息，既无具名检查也无数值偏差——报告会显式点明这一点，而不会假装证据是完整的。存在报告时，`build_check_stats_from_report()` 也会填充 `verifier_check_stats`，使检查定位与置信度评分同样适用于 Harbor 报告。
- **`error_observations`** 可携带 Harbor 运行阶段条目，附带 `stage: "runner"`、`runner_phase: "post_agent_execution"`、`scoring_reached` 与 `missing_deliverable` 等字段。相关码：`INFRA_ARTIFACT_COLLECTION_CRASH`（`Engine cp failed ... Could not find the file /app/...`）、`INFRA_RUNNER_INTERRUPTED`（日志止于 `Collecting main service artifacts`），以及作为因果项的 `INFRA_AGENT_TIMEOUT`。这些在时间上位于 Agent 执行*之后*、验证*之前*，因此绝不可被归入 `environment_build` / `container_build` 阶段。无害的 `Cannot estimate Claude cost ... litellm.BadRequestError: LLM Provider NOT provided` 行是遥测信息，在成功 Trial 中同样存在，故刻意不设为其错误规则。

<a id="4-附加交叉核对交付物完整性与因果溯源扩展"></a>

## 4. 数值核对与溯源扩展

以下字段均为 `evidence.json` / `summary.json` 中的附加可选扩展：

以下是字段示意，不是真实运行记录；仅展示部分字段。

<details>
<summary>展开数值核对与溯源示例</summary>

```json
{
  "deliverable_diagnostics": {
    "scanned_files": ["artifacts/app/results.json", "artifacts/app/summary.csv"],
    "empty_deliverables": [],
    "nan_or_inf_values": [
      {"file": "artifacts/app/summary.csv", "key": "force_max", "value": "NaN", "row": 3}
    ],
    "missing_checked_keys": ["converged_energy"]
  },
  "scientific_observations": [
    {
      "sci_id": "sci:recompute_div:1",
      "error_family": "verifier_recompute_divergence",
      "metric": "total_energy",
      "agent_reported_value": -15.42,
      "recomputed_value": -419.60,
      "scale_or_sign_hint": {
        "kind": "unit_conversion",
        "factor": 0.036749,
        "label": "Hartree/eV (÷27.211386)",
        "unit_token_corroborated": false
      },
      "stale_deliverable_sync": true,
      "recomputed_match_in_trajectory_ref": "trajectory:step:18"
    }
  ],
  "causal_anchors": {
    "algorithm_or_param_definition": {
      "event_id": "trajectory:step:9",
      "parameter_evolution": [
        {
          "param": "ecutwfc",
          "file": "/app/code/solve.py",
          "initial": "30",
          "final": "80",
          "from_event": "trajectory:step:4",
          "to_event": "trajectory:step:9"
        }
      ]
    }
  }
}
```

</details>

- **多格式轨迹**：`resolve_trajectory_path` 和 `_convert_messages_to_steps` 解析可识别的 ATIF、OpenAI 和 Anthropic JSON/JSONL 结构，按工具调用 ID 关联调用与结果。`truncate_with_head_tail` 可保留中部最多 6 条报错或指标行。`script_targets` 静态提取脚本输出路径，在匹配的 shell 调用未检测到错误时加入 `written_files`；这不证明文件已实际生成。
- **具名指标配对**：`pair_verifier_evidence_with_artifacts` 按字段名精确、规范化或子串匹配配对数值，不因偏差过大而丢弃具名候选。无指标名时仍要求相对偏差小于 `0.05` 或绝对偏差小于 `0.05`。字段名相似仍需人工核对含义。
- **`scale_or_sign_hint`**：根据两非零有限数值的比例匹配预设单位系数（`27.211386` Hartree/eV、`627.5095` Hartree/kcal/mol、`23.06055` eV/kcal/mol、`96.4853` eV/kJ/mol、`4.184` kcal/kJ、`0.529177` Bohr/Angstrom）或十进制倍率（`10`、`100`、`1000`），允许 1.5% 相对误差；反号匹配允许 2% 幅值误差。`unit_token_corroborated` 标记 `metric` 或验证器文本中是否出现了预期单位词；`factor` 表示交付值相对复算值的倍率，不是对交付值直接施加的修正系数，仍为待核实的提示。
- **`stale_deliverable_sync` 与 `recomputed_match_in_trajectory_ref`**：在非 delta 分歧中，仅当 `trial_dir` 磁盘上存在匹配 `metric` 与 `reported_value`（且尚未匹配 `recomputed_value`）的交付文件，并且某步无报错的执行 `observation`（不包含 `tool_calls` 输入代码）包含 `metric` 与接近复算值的数值（至少 4 位有效数字，误差不超过 `max(1e-9, abs(recomputed_value) * 5e-4)`）时触发。它仍未验证计算与落盘时间戳先后，仅作待核实线索。
- **`deliverable_diagnostics`**：扫描 `artifacts/` 下的 JSON/CSV/TSV，字段为 `scanned_files`、`empty_deliverables`、`nan_or_inf_values`、`results_json_files`、`results_json_keys` 和 `missing_checked_keys`。上限为 200 个文件、每文件 8,000,000 字节、每表 200,000 行、每 JSON 20,000 个节点。缺键检查比较所有已找到的 `results.json` 的键名并集；没有此文件时不产生缺键条目。无法解析或超限的内容可能被跳过，诊断为空不表示交付物完整。相关 `contract_observations` 还要求 `verify.log` 中有对应失败信息。
- **`parameter_evolution`**：按目标脚本文件（`file`）作用域聚合识别到的科学参数赋值（`cutoff`、`eta`、`alpha`、`tol`、`tolerance`、`ecutwfc`、`ecutrho`、`dt`、`timestep`、`kpoints`、`conv_thr`、`mixing_beta`、`nsteps`），覆盖写/改文件、heredoc 及指向该脚本的命令；当首末值不同时记录 `file`、`initial`、`final`、`from_event` 和 `to_event`，并按 `algo_anchor.target_path` 过滤后附加至 `causal_anchors.algorithm_or_param_definition`。它不保留完整中间历史，也不证明参数在运行时生效。关联失败产物的生成候选另有 `+15` 排序加分；两者均为定位线索。

## 5. 已有证据审查字段

| 字段 | 含义 |
| --- | --- |
| `evidence.deliverable_audit` | 声明、路径映射、采集状态、扫描覆盖和验证器明确缺失记录；不推断未生成 |
| `evidence.trajectory_normalization` | 格式、覆盖程度、诊断与源指针空间 |
| `analysis.causal_assessment.level` | `not_applicable`、`observed_failure`、`candidate_cause` 或 `evidence_supported_cause` |
| `causal_assessment.existing_verification` | 既有检查及数值记录、声明覆盖和限制；`new_calculation_performed` 为 false |
| `causal_assessment.decision` | 具体决策、实际执行、有效输入输出、适用依据、最终观测和推理 |
| `causal_assessment.alternatives` | 各替代解释的状态、理由和证据引用；未解决项保留 |
| `causal_assessment.capability` | 当前试次范围内的疑似指导，不能单独确认模型能力不足 |

审查记录绑定输入 JSON 和保存来源摘要，详见[已有证据审查](evidence-review.md)。这些字段记录证据和审阅判断，不保证科学解释绝对正确。

## 6. 评分契约与运行阶段复核

| 字段 | 用途 |
| --- | --- |
| `runtime.score_produced` | 是否实际产出可识别分数；验证器启动不等于评分完成 |
| `runtime.reward_metrics` | 原始命名的数值指标，如 `submission_checks`；不改称科学准确率 |
| `contract_review.review_items` | `semantic_rejection`、`hidden_format_requirement`、`definition_ambiguity`，保留公开要求、实际提交、失败规则和覆盖限制 |
| `contract_review.execution_scope_findings` | 原始采集产物与隔离重放产物的检查范围差异 |
| `contract_review.requirement_coverage_findings` | 直接标签键、嵌套标签出现位置与原文中的核验状态，避免只按表层结构推断覆盖缺失 |
| `scientific_contract_review.findings` | 由已保存源码、公开要求、实际执行和失败检查关联的科学定义差异；保持候选状态 |

评分器启动后异常且未产出分数时，`verification_status` 为 `errored`。有运行异常但仍产出零分的试次保留评分失败和运行异常两个事实，不重复计数。契约复核项不会将原始失败改判为通过；第一个拒绝条件之后的检查可能没有执行。
