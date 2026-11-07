# 逆向因果归因协议 (`Causal Attribution Protocol`)

**[English](../en/attribution-protocol.md) | [简体中文](attribution-protocol.md)**

每次失败因果审计都必须遵循以下 7 步逆向因果追踪协议，严禁从单行错误日志直接跳向根因结论。

---

## 1. 逆向因果追踪七步法 (`Backward Causal Trace Order`)

针对每一个失败的 Trial：

1. **识别最终失败表象 (`Identify the Final Failure Manifestation`)**
   - 确认导致该 Trial 被判失败的确切表象（`reward < 1.0`、`verify.log` 中的 `FAIL: ...` 或 `exception_info != null`）。
   - 注意：若 `reward >= 1.0`（`verification_status == "passed"`），则该 Trial 已通过，**绝不**因 Job 级错误统计 `stats.n_errored_trials` 污染该 Trial。
2. **回溯直接产物状态 (`Trace Backward to the Immediate Artifact State`)**
   - 检查缺失或无效文件/数值是如何产生的，核查三方契约矩阵：`instruction.md`（Prompt 要求） vs `tests/verify.py`（评测器要求） vs Agent 实际产物。
3. **定位首次错误决策或异常状态 (`Locate the First Error Decision or Abnormal State`)**
   - 沿标准化轨迹事件（`trajectory:step:N`）逆向回溯，找到状态首次偏离合法求解路径的位置。
4. **评估可恢复性 (`Assess Recoverability`)**
   - 区分容器内可由 Agent 自主恢复的问题与不可恢复的外部/环境缺陷（如容器构建失败、题目缺失资产文件、或完成因果绑定的评测器解析缺陷/内部崩溃）。
5. **核查 Agent 观测状态 (`Check Agent Observation`)**
   - 检查 Agent 是否在工具输出（`observation`）中观测到了报错信息或异常数值。
6. **核查 Agent 恢复动作 (`Check Agent Recovery Actions`)**
   - 检查 Agent 是否诊断了错误并合理调整了物理/计算参数，还是未做实质修改就重复执行失败动作（`repeated_failed_action`）或直接忽略错误。
7. **锁定首次不可恢复偏离点 (`first_unrecovered_deviation`)**
   - 记录 `{"status": "identified", "event_ref": "<valid_id>", "summary": "..."}`；若因日志缺失无法定位，显式记录 `{"status": "not_identified", "event_ref": null, "summary": "..."}`。

---

### 2. 因果时间线与确定性门控求值顺序 (`Causal Precedence Timeline & Gate Evaluation Order`)

所有因果归因严格遵循客观时序与依赖链：
$$\text{Infra (启动前环境)} \longrightarrow \text{Case (题目资产/配置)} \longrightarrow \text{Verifier (评测器崩溃/解析/容差)} \longrightarrow \text{Agent (正向偏离动作)}$$

为在代码中确定性实现该时序，引擎按声明顺序（gate0–gate6 共 9 个决策 id）依次求值各个 Gate：
1. **Gate 0: 通过短路门 (`gate0_passed`)**：若 Trial 已通过验证（`verification_status == "passed"` 或 `reward >= 1.0`），则不存在需要归因的失败；引擎以 `category = "none"` 短路返回，绝不因 Job 级错误统计污染已通过的 Trial。
2. **Gate 1a: 启动前环境基础设施 (`gate1a_infra_prestartup`)**：`agent_started == false` 且存在致命基础设施故障证据时，归因为 `infra`。
3. **Gate 1b: 启动前静默 (`gate1b_unknown_no_evidence`)**：`agent_started == false` 且无任何日志或错误可解释原因时，保守归因为 `unknown`。
4. **Gate 1c: 验证前 Runner / 执行基础设施 (`gate1c_runner_artifact_collection`)**：发生在 Agent 执行之后、验证之前的 Harbor 故障——`INFRA_ARTIFACT_COLLECTION_CRASH`（`Engine cp failed`）、`INFRA_RUNNER_INTERRUPTED`（日志止于 `Collecting main service artifacts`）或作为因果项的 `INFRA_AGENT_TIMEOUT`——均归因为 `infra`。上游的 Agent 超时优先于下游的产物采集崩溃；Agent 缺失的已声明交付物作为显式的伴随因素保留。此类 Trial 大多验证器从未启动，因此**不存在已评分失败**，且严禁判为 `agent` / `AGENT_PREMATURE_TERMINATION`。
5. **Gate 2: 题目资产与配置缺陷 (`gate2_case_definition`)**：题目要求资产缺失或镜像配置损坏，归因为 `case`。
6. **Gate 3: 评测器缺陷 (`gate3_verifier_defect`)**：评测器自身内部崩溃（`VERIFIER_RECOMPUTE_DEFECT`）或完成直接因果绑定的解析正则缺陷（`failure_binding == "direct"`），归因为 `verifier`。
7. **Gate 4: 数值漂移与容差缺陷 (`gate4_numerical_divergence`)**：结构化瞬时轨迹发散但系综均值守恒吻合、且三方契约闭环成立时，归因为 `verifier` (`VERIFIER_TOLERANCE_TOO_STRICT`)。
8. **Gate 5: 正面证据不足门控 (`gate5_insufficient_positive_evidence`)**：若 Trial 失败但缺乏指向 Agent 行为的正面因果证据，保守弃判并退化至 `unknown`，防止误怪 Agent（仅有 `reward == 0` 或 `verify.log` 中的 `FAIL` 不构成正面证据）。这是 Agent 归因前的保守兜底。
9. **Gate 6: Agent 主观决策与操作错误 (`gate6_agent_primary`)**：在满足 5 项必要条件且具备正面行为信号时归因为 `agent`。若智能体自检通过、但验证器给出了具名失败检查，则 5e 子情形使用 `self_check_gap` 亚类（`AGENT_SELF_CHECK_GAP`），并记录自检脚本覆盖了哪些具名检查、遗漏了哪些。该 Gate 必然命中，从而终结整条判定链。

| 阶段顺序 | 对应门控 | 适用范围 / 触发条件 | 归因类别 | 绝对禁止项 |
| :--- | :--- | :--- | :--- | :--- |
| **阶段 0: Trial 已通过** | Gate 0 | `verification_status == "passed"` 或 `reward >= 1.0` | `none`（无失败可归因） | 无失败归因 |
| **阶段 1: 启动前环境** | Gate 1a | `agent_started == false` 且存在致命基础设施错误 | `infra` | 严禁归因 `agent` |
| **阶段 1b: 启动前静默**| Gate 1b | `agent_started == false` 且无任何异常日志证据 | `unknown` | 严禁归因 `agent` |
| **阶段 1c: 验证前 Runner** | Gate 1c | Agent 执行后的产物采集崩溃 / 静默中断 / Agent 超时 | `infra` | 严禁归因 `agent`；验证器未启动时严禁判为已评分的 `failed` |
| **阶段 2: 题目资产配置** | Gate 2 | 缺失 Prompt 资产文件或题目镜像配置损坏 | `case` | 严禁归因 `agent` |
| **阶段 3: 评测器崩溃/解析**| Gate 3 | 评测器内部崩溃或直接绑定的解析器缺陷 | `verifier` | 严禁归因 `agent` 或判为 `unknown` |
| **阶段 4: 数值容差缺陷** | Gate 4 | 瞬时轨迹发散但系综统计量吻合且闭环成立 | `verifier` | 严禁未经验证归因 `agent`（不完备时弃判）|
| **阶段 5: 证据不足** | Gate 5 | Trial 失败且无指向 Agent 的正面证据 | `unknown` | 严禁无正面证据归因 `agent` |
| **阶段 6: Agent 决策偏离** | Gate 6 | Agent 正向行为偏离/轨迹操作错误 | `agent` | 严禁无正面证据直接推定 Agent 责任 |

### 归因 Agent 的五大必要条件与硬规则 (`Necessary Gate Conditions for Blaming the Agent`)

仅当以下 **5 项必要条件全部成立** 且存在至少一项 **Agent 正面证据** 时，才允许将 `primary_root_cause.category` 判为 `"agent"`：

1. **任务完备性 (`Task Completeness`)**：`instruction.md` 提供了完整明确的规范说明，且所需资产文件在容器内真实存在。
2. **基础设施完整性 (`Infrastructure Integrity`)**：`runtime.agent_started == true`，且没有任何致命的容器/网络/API/超时故障阻断正常执行。
3. **评测器契约一致性 (`Verifier Contract Alignment`)**：失败的评测检查对应 `instruction.md` 的显式要求，既不是已完成因果绑定的解析器缺陷（`failure_binding == "direct"`），也不是评测器自身崩溃（`verifier_internal_crash`）。
4. **Agent 正面证据 (`Positive Agent Evidence`)**：`evidence_refs` 中至少引用了一项明确的 Agent 正面信号（`behavioral_signals`、`agent_mismatch` 契约违约、Agent 轨迹决策事件、或由 Agent 操作引发的 `scientific_observations`）。仅有 `reward=0` 或 `verify.log` 中的 `FAIL` **不构成**充分证据。
5. **因果闭环 (`Causal Link`)**：修正所定位的 Agent 决策或操作即可直接消除最终失败表象。

> **启动前失败硬规则 (`runtime.agent_started == false`)**：
> - 若 `runtime.agent_started == false` 且存在致命基础设施故障证据（`causal_candidate == true`）→ `primary_root_cause.category` **必须**为 `infra`（若系题目 `Dockerfile` 自身语法/配置损坏则为 `case`）。
> - 若 `runtime.agent_started == false` 且无任何基础设施或异常日志 → `primary_root_cause.category` **必须**为 `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`)。
> - 只要 `runtime.agent_started == false`，将 `primary_root_cause.category` 判为 `"agent"`（误判归因 Agent）属于**绝对禁止行为**。

> **验证器未运行 Trial 硬规则 (`runtime.verifier_started == false` 或 `runtime.verdict in ("unknown", "not_run")`)**：
> - 当验证器从未启动时，单凭 `sig:premature_completion` 轨迹启发式**不构成** Agent 正面证据 (`VERIFIER_NOT_RUN_PREMATURE_BLAME`)。要归因为 `agent`，必须在 `evidence_refs` 中引用未完成观测（未恢复的科学报错、`agent_mismatch` 契约违约、`wrong_output_path` 等）。
> - Harbor 的已声明交付物来自 `task.toml` 的 `artifacts`，而非硬编码的 `results.json`。`premature_completion` 信号仅当某个*已声明*路径确实缺失、且与采集到的 `<trial>/artifacts/app/...` 目录树对账后成立时才有效。
> - 发生在 Agent 执行之后 / 验证之前窗口内的 Runner 侧故障（Gate 1c）仍以 `infra` 为主根因；Agent 未完成的交付物是伴随因素而非根因。该 Trial **不予评分** (`reward=None`)，且不得在批次通过率中被计为已评分失败。

---

## 3. 区分评测器缺陷与 Agent 错误 (`Distinguishing Verifier Defects vs Agent Errors`)

当 `verifier/verify.log` 输出报错或 `FAIL: <msg>` 时：

- **检查 1 — 评测器内部崩溃 (`VERIFIER_RECOMPUTE_DEFECT`)**：
  若 `verify.py` 因自身内部路径或文件缺失崩溃（例如 `FileNotFoundError: /tmp/verify_tmp/refs.json`、或与 Agent 必选产物无关的未捕获 `Traceback`），必须归因为 `verifier` (`VERIFIER_RECOMPUTE_DEFECT`)，**严禁**判为 `agent`。
- **检查 2 — 完成因果绑定的解析器/正则缺陷 (`failure_binding == "direct"`)**：
  `verify.py` 中的静态解析风险（如未支持 Fortran `D+03` 指数或 namelist 字符串中的 `/` 截断）**仅在** `failure_binding == "direct"`（即失败字段确实因该正则/解析器而解析失败）时才可判为 `verifier`。若 `1.23D+03` 仅作为参考值打印在日志中而 Agent 实际输出了错误数值 `9.99E+02`，则 `failure_binding` 为 `"none"`，不得归咎于 `verifier`。
- **检查 3 — 隐式列序或格式假设 (`VERIFIER_HIDDEN_CONTRACT`)**：
  若 `verify.py` 不解析 `Step` 表头名称而直接按 `.split()` 固定下标取列，且 `instruction.md` 未规定热力学输出列顺序、同时 Agent 的 `results.json` 已通过参考值核验，则应归因为 `verifier` (`VERIFIER_HIDDEN_CONTRACT`)。

---

## 4. 数值漂移与竞争假设规则 (`Numerical Attribution & Competing Hypothesis Rules`)

- **严格三方闭环数值验证 (`Closed-Loop Numerical Verification`)**：数值轨迹漂移判定必须同时满足以下三方条件：
  1. **Prompt 契约核验**：`instruction.md` 必须真实存在且**未**要求逐点完全重合的瞬时轨迹。若缺失 `instruction.md`，契约不完备，必须保守弃判退化至 `unknown`。
  2. **可执行 AST 代码检查**：`tests/verify.py` 必须在条件或数值比较表达式（如 `ast.Assert` 的 `node.test`、`ast.Compare` 的比较操作数或断言调用参数）中真正评估了瞬时轨迹/坐标。若相关词汇仅出现在注释、文档字符串、断言报错提示信息（`node.msg`）或字符串包含判断（`"..." in log`）中，不构成评测缺陷，必须保守弃判。
  3. **运行时直接因果绑定**：`verifier/verify.log` 必须直接将失败断言绑定到瞬时 RMSD，且同时记录有系综均值或守恒量吻合。非数值异常（如文件缺失 `FileNotFoundError`、字典缺键 `KeyError` 等）以及 pytest 测试用例函数名绝不可被误绑定为数值容差缺陷。
  4. 满足以上三项，方可判定为主根因 `verifier` (`VERIFIER_TOLERANCE_TOO_STRICT`)。
- **竞争假设必填**：每一个失败或异常用例都必须填充 `competing_hypotheses`（对应 `candidate-hypotheses.json`）及 `first_unrecovered_deviation`。
- **校准的证据强度评分**：置信度评分（`confidence_kind = "heuristic_evidence_score"`，`evidence_strength = "high" | "medium" | "low"`）由支持证据点数、多源交叉印证及竞争假设区分度确定性计算得出（见 `scripts/confidence.py`）。

---

## 5. 产物所有权清单与事务发布机制 (`Artifact Ownership & Transactional Publication`)

- **清单所有权追踪与越界隔离**：输出目录中通过 `.cfa_manifest.json` 显式记录 CFA 管理的文件与目录清单。清单路径经严格安全性校验，彻底阻断路径穿越（`..`）、绝对路径及溢出输出根目录的非法路径。
- **严格保护用户非受管文件**：在缺乏有效清单时，CFA 假定管理 0 个原有文件。用户自行创建的任意子目录及文件（如 `user_dir/report.md`、`custom_notes.txt`）受到严格保护，绝不被盲目当作旧产物清理。
- **单文件原子替换与进程并发互斥**：发布产物先写入同级隐藏临时文件，再通过 `os.replace` 原子替换就位，彻底避免并发读取端看到半写入的损坏文件。针对同一输出根目录的并发写入采用进程独占排他锁进行串行化控制，并自动清理异常退出遗留的孤儿临时文件。
- **尽力而为事务回滚**：在新版本产物全部原子就位后才清理旧受管产物。在发布异常时触发尽力而为回滚恢复备份，次生 I/O 报错均被隔离记录至 stderr，确保如实向上抛出主异常。
