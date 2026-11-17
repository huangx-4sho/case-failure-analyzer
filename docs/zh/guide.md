# case failure analyzer — 系统工作流与归因总规范

**[English](../en/guide.md) | [简体中文](guide.md)**

当需要诊断科学计算基准测试用例（`jobs/<job_name>` + `tasks/<task_name>`）为何失败时，使用本工具集与 Skill。本系统执行端到端、基于客观证据链的因果审计，而非朴素的日志关键词匹配：

**产物发现 (`Artifact Collection`) → 统一 Trial 运行态门控 (`Runtime State`) → 时间线重建 (`Timeline Reconstruction`) → 三方契约审计 (`Contract Audit`) → 科学软件错误诊断 (`Scientific Error Diagnosis`) → 候选假设竞争 (`Candidate Hypotheses`) → 根因裁决 (`Causal Attribution`) → 中英双版报告与技能处方生成 (`Bilingual Report & Skill Prescription`)**

---

## 输入参数 (`Inputs`)

需要或自动发现以下路径：

- `--job`：作业目录（`jobs/<job_name>` 或 `jobs_failed_backup/<job_name>`）或单个 Trial 目录
- `--task`：对应的任务定义目录（`tasks/<task_name>`，可省略并自动解析）

> **输入校验**：`--job` / `--task` 路径必须存在，`--trial` 必须在作业下真实存在，否则入口脚本以退出码 `2` 快速失败，不产出任何报告。仅当路径存在、无 Trial 目录但存在 job 级证据（`result.json` 或 `job.log`）时，才按“启动前 Job 级失败”继续分析。

---

## 默认行为与边界 (`Default Behavior & Boundaries`)

- **默认严格只读**：绝不修改 `tasks/`、`jobs/` 或 `tests/verify.py`。
- **确定性取证 + 模型因果推理**：确定性脚本负责生成客观 `evidence.json` 与候选假设 `candidate-hypotheses.json`；默认 `--phase all` 会输出保守的确定性草稿 `analysis.json`，可由 Agent/模型依据 [attribution-protocol.md](attribution-protocol.md) 覆盖式改写并核验。
- **证据优先归因**：严禁仅凭单一关键词直接定性根因；严格区分**直接失败现象 (`failure_manifestation`)**、**暴露阶段 (`detection_stage`)** 与**根本原因 (`primary_root_cause`)**。
- **指责 Agent 必须具备正面证据**：禁止采用“排除其他 Gate 后默认归因 Agent”的兜底逻辑。归因 `agent` 必须引用明确的正面证据（如 `behavioral_signals`、`agent_mismatch` 契约违约、或 Agent 操作引发的 `scientific_observations`）。
- **校准的不确定性**：当现有证据无法区分竞争假设时，诚实输出 `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`)。
- **中英双语报告产出**：默认生成合订本 `report.md`、纯中文版 `report.zh.md` 与纯英文版 `report.en.md`（当满足条件生成 `skill-prescription.md` 时同样包含双语对照）。

---

## 核心工作流 (`Workflow`)

1. **运行产物收集与统一 CLI 审计**
   使用统一 CLI（`scripts/cfa.py`）执行任务 ID 审计、只读脱敏检视与跨任务/跨试次对比，或对已知路径调用 `scripts/analyze_case.py`：
   ```bash
   SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
   [ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
   python3 "$SKILL_DIR/scripts/cfa.py" audit task208 --jobs-root <benchmark checkout> --lang zh
   python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --section artifacts
   python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --section step --step 43
   python3 "$SKILL_DIR/scripts/cfa.py" diff task207 task208
   # 或按显式目录路径运行：
   python3 "$SKILL_DIR/scripts/analyze_case.py" \
     --job jobs/<job_name> \
     --task tasks/<task_name> \
     --output failure-analysis/<case_id>
   ```
   `cfa.py inspect` 支持 `--section verdict|report|errors|confidence|evidence|anchors|artifacts|step|data`（默认自动脱敏，支持 `--raw` 与 `--no-redact`）；`cfa.py diff <target_a> [<target_b>] [--trials <t1> <t2>]` 用于对比两个任务或同一任务两个试次的裁决、主因、失败检查与因果锚点差异。
2. **单 Trial 启动与基础设施状态门控 (`scripts/runtime_state.py` + `scripts/extract_runtime_errors.py`)**
   基于单 Trial 证据判定 `agent_started`、`verifier_started`、`execution_status`、`verification_status` 与 `verdict`。若 `reward >= 1.0` 则直接判定为 `passed`，绝不因 Job 级汇总统计 `stats.n_errored_trials > 0` 误判通过的 Trial。
3. **重建统一执行时间线与因果锚点 (`scripts/normalize_trajectory.py` + `scripts/trace_causal_anchors.py`)**
   将 `ATIF-v1.7`、`.jsonl` 及 OpenAI/Anthropic `tool_use`/`tool_result` 消息流跨步折叠标准化为时间线事件与行为信号（`behavioral_signals`），结合保留中段高显著性错误/指标行的截断策略与两阶段脚本输出目标图（`script_targets`）精准识别交付物写出；`trace_causal_anchors.py` 对关联失败产物的脚本执行/写入事件给予反向溯源加权，并提取关键科学参数的 `parameter_evolution` 演化轨迹。
4. **审计三方契约、交付物完整性与评测器缺陷 (`scripts/audit_contract.py`)**
   比对 `Prompt <-> Verifier <-> Agent Output`，扫描 `artifacts/` 下的 JSON/CSV/TSV 交付物生成 `deliverable_diagnostics`（检测空表、`NaN`/`Inf` 数值与 `checked_keys` 缺失），并对 `tests/verify.py` 静态解析风险做因果绑定 (`failure_binding: direct | indirect | none`)，同时独立识别 Verifier 自身崩溃 (`verifier_internal_crash` / `VERIFIER_RECOMPUTE_DEFECT`)。
5. **提取科学计算软件错误与交叉核对事件 (`scripts/extract_scientific_errors.py`)**
   基于单一真值源 `references/error-families.json` 匹配 11 类科学计算与机器学习势套件的 60 类 `error_family`（支持 `.jsonl` 轨迹印证、无界具名指标交付物配对、`scale_or_sign_hint` 单位/量纲/符号识别以及 `stale_deliverable_sync` 过时交付同步检测），并结合 `references/software/<software>.md` 判别证据要求。
6. **定位首次不可恢复偏离点 (`first_unrecovered_deviation`)**
   记录 `{"status": "identified", "event_ref": "...", "summary": "..."}`；若因日志缺失无法定位，显式记录 `{"status": "not_identified", "event_ref": null, "summary": "..."}`。
7. **评估竞争根因假设 (`scripts/generate_hypotheses.py` & `scripts/confidence.py`)**
   对候选假设（`H1`、`H2`...）列出支持证据 (`evidence_for`)、反对证据 (`evidence_against`)、缺失证据 (`missing_evidence`) 与反事实检验 (`counterfactual_test`)，并计算启发式证据强度评分 (`confidence_kind="heuristic_evidence_score"`，`evidence_strength="high|medium|low"`)。
8. **校验并渲染中英双版输出 (`scripts/validate_analysis.py` & `scripts/render_report.py`)**
   通过 `validate_analysis.py` 强制校验 Schema、竞争假设、首次偏离点、Agent 正面证据及 4 个核心报告章节非空正文，最后输出 `report.md`、`report.zh.md`、`report.en.md` 及可选的 `skill-prescription.md`。

---

## 归因硬性约束 (`Attribution Hard Rules`)

1. **严禁仅凭单一关键词定根因**：日志中孤立出现 `chaotic` 等词汇而无轨迹/系综统计对比数据时，严禁判定为数值发散容差问题（无充分对比数据时必须归因 `unknown`）。
2. **严格区分失败现象、检测阶段与责任根因**。
3. **Agent 未启动硬规则 (`runtime.agent_started == false`)**：
   - 若 `agent_started == false` 且存在致命基础设施错误证据 → 必须归因 `infra`；
   - 若 `agent_started == false` 且没有任何异常或构建日志证据 → 必须归因 `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`)；
   - 若 `agent_started == false` → **绝对禁止**归因 `agent`。
4. **归因 `agent` 必须具备正面证据**：仅有 `reward=0` 或 `verify.log` 报错不构成 Agent 正面证据。
5. **静态解析 Hazard 必须完成因果绑定 (`failure_binding == "direct"`)**：若 `verify.log` 仅在参考值中打印 `1.23D+03` 而 Agent 实际输出了错误数值 `9.99E+02`，严禁误判为 Verifier 正则缺陷；反之，若 Verifier 因自身临时文件缺失（如 `FileNotFoundError: /tmp/verify_tmp/refs.json`）崩溃，必须归因 `verifier` (`VERIFIER_RECOMPUTE_DEFECT`)。
6. **证据不足或竞争假设无法区分时必须输出 `unknown`**。
7. **仅针对可复用的 Agent 科学/工程能力缺口生成 `skill-prescription.md`**：严禁为绕过 Infra 故障或 Verifier 缺陷而生成领域 Skill。

