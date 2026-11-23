# 归因协议

**[English](../en/attribution-protocol.md) | [简体中文](attribution-protocol.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="逆向因果归因协议-causal-attribution-protocol"></a>

**本页：** [逆向追踪流程](#1-逆向追踪流程) · [门控求值顺序](#2-门控求值顺序) · [区分验证器缺陷与智能体错误](#3-区分验证器缺陷与智能体错误) · [数值分歧与竞争假设](#4-数值分歧与竞争假设) · [产物所有权与发布](#5-产物所有权与发布)

当已记录的失败需要进一步解释因果过程时，按以下 7 步逆向追踪。快速审计在已有证据足够明确时，会直接列出定位到的评分拒绝或运行中断。

<a id="1-逆向因果追踪七步法-backward-causal-trace-order"></a>

## 1. 逆向追踪流程

针对每个失败的试次：

1. **识别最终失败表象**
   - 结合单试次的最终结论与 `score_produced` 判断是否产出有效分数，明确终止运行的事件、未通过的检查项，或两者兼有的情况。试次在记录了异常的同时仍可能已通过并产出分数；不要用作业级统计 `stats.n_errored_trials` 替代单个试次的判定。
2. **回溯直接产物状态**
   - 检查缺失或无效的文件与数值是如何产生的，核对三方契约矩阵：`instruction.md`（任务要求） vs `tests/verify.py`（验证器检查） vs 智能体输出。
3. **定位首次错误决策或异常状态**
   - 沿标准化轨迹事件（`trajectory:step:N`）逆向回溯，找到状态首次偏离合法求解路径的位置。
4. **评估可恢复性**
   - 区分容器内可由智能体调整恢复的问题与不可恢复的缺陷（如容器构建失败、题目缺失资产文件，或与拒绝直接关联的验证器解析缺陷/内部崩溃）。
5. **核查智能体观测状态**
   - 检查智能体是否在工具输出中看到了报错信息或异常数值。
6. **核查智能体恢复动作**
   - 检查智能体是否根据报错调整了参数，还是未作修改就重复失败动作（`repeated_failed_action`）或直接忽略错误。
7. **锁定首次不可恢复偏离点（`first_unrecovered_deviation`）**
   - 记录 `{"status": "identified", "event_ref": "<valid_id>", "summary": "..."}`；日志不完整时记录 `{"status": "not_identified", "event_ref": null, "summary": "..."}`。把失败归因于智能体决策时，必须绑定具体执行事件（如工具调用、文件写入或命令输出），思考过程和最终回复只作背景参考。`evidence_refs` 中的每项引用都必须在 `evidence.json` 中有对应记录。

<a id="2-因果时间线与确定性门控求值顺序-causal-precedence-timeline--gate-evaluation-order"></a>

## 2. 门控求值顺序

引擎在 `decision_trace` 中记录检查过的规则，并返回首个匹配结果。若较早的运行故障可以解释后续拒绝，先处理运行故障。

1. `gate0_review_pending` 处理已有独立复核请求、尚无最终分数的试次。`gate0_passed` 将通过试次的类别记为 `none`。
2. `gate1a_infra_prestartup` 检查智能体启动前的致命故障。构建日志可以区分本地工具缺失、镜像仓库拒绝访问和令牌请求失败。缺少可解释启动失败的记录时，`gate1b_unknown_no_evidence` 保留 `unknown`。
3. `gate1c_runner_artifact_collection` 检查产物采集失败、运行器中断、智能体超时和模型服务配额耗尽。超时可能早于采集失败。验证器随后运行时，报告仍保留其拒绝记录和原始分数。归因于配额中断需要终止证据，已恢复的 429 不能单独定因。若评分因独立的验证器故障中止，另查该故障。
4. `gate2_case_definition` 核对题目资产和运行配置。某个文件可能由镜像构建步骤从已有分片还原，审查缺件时需核对构建脚本和分片清单。
5. `gate3_verifier_defect` 检查验证器自身崩溃，或与此次拒绝有直接联系的解析缺陷。
6. `gate4_numerical_divergence` 核对相互印证的数值比较。若差异能追到提交数据，可形成智能体候选原因；若系综统计一致且公开约定允许，则可审查验证容差是否过严。
7. `gate5_insufficient_positive_evidence` 在试次失败且现有记录无法指向智能体决策时返回 `unknown`。
8. `gate6_agent_primary` 从其余证据中选出智能体错误的候选原因。具名评分拒绝优先于笼统的提前结束信号。审查 `AGENT_SELF_CHECK_GAP` 时，要将实际运行的自检及脚本版本与公开判据、终态拒绝对应起来。[自检证据](evidence-review.md#具体失败位置与自检证据)

<details>
<summary>展开门控速查表</summary>

| 阶段顺序 | 对应门控 | 适用范围 / 触发条件 | 归因类别 | 限制项 |
| :--- | :--- | :--- | :--- | :--- |
| 评分待审 | Gate 0 | 已记录独立复核请求，尚无最终分数 | `none` | 保留 `pending` |
| **阶段 0: 试次通过** | Gate 0 | `verification_status == "passed"` 或 `reward >= 1.0` | `none`（无失败可归因） | 无失败归因 |
| **阶段 1: 启动前环境** | Gate 1a | `agent_started == false` 且存在致命基础设施错误 | `infra` | 不得归因 `agent` |
| **阶段 1b: 启动前静默** | Gate 1b | `agent_started == false` 且无基础设施证据 | `unknown` | 不得归因 `agent` |
| **阶段 1c: 运行器或模型服务** | Gate 1c | 采集失败、运行器中断、智能体超时或模型服务配额终止 | `infra` | 核对验证器此后是否运行并产出分数 |
| **阶段 2: 题目资产配置** | Gate 2 | 缺失题目资产文件或题目容器配置损坏 | `case` | 不得归因 `agent` |
| **阶段 3: 验证器崩溃或解析缺陷** | Gate 3 | 验证器内部崩溃或直接绑定的解析缺陷 | `verifier` | 孤立崩溃时不得归因 `agent` 或 `unknown` |
| **阶段 4: 数值证据** | Gate 4 | 经印证的复算分歧，或系综统计吻合而瞬时容差过严 | `agent` / `verifier` | 不凭未印证的数值差异归因 |
| **阶段 5: 证据不足** | Gate 5 | 试次失败且无指向智能体的正面证据 | `unknown` | 无正面证据不得归因 `agent` |
| **阶段 6: 智能体决策偏离** | Gate 6 | 存在正向的智能体行为或轨迹错误证据 | `agent` | 无正面证据不得选定 `agent` |

</details>

<a id="归因-agent-的五大必要条件与硬规则-necessary-gate-conditions-for-blaming-the-agent"></a>

### 归因到智能体的必要条件

复核时可用以下条件支持智能体原因。`cfa.py run` 自动选出的 `agent` 只作候选原因；若竞争解释尚未排除，结论停留在候选层面。

1. **任务完备性**：`instruction.md` 提供了完整明确的规范说明，且所需资产文件在容器内存在。
2. **基础设施完整性**：`runtime.agent_started == true`，且执行过程没有因容器、网络、API 或超时故障而中止。
3. **验证契约一致性**：失败检查对应 `instruction.md` 中的显式要求，既不是直接因果绑定的解析缺陷（`failure_binding == "direct"`），也不是验证器内部崩溃（`verifier_internal_crash`）。
4. **智能体正面证据**：`evidence_refs` 中至少引用了一项明确的智能体正面信号（`behavioral_signals`、`agent_mismatch` 契约违约、智能体轨迹决策事件，或由智能体操作引发的 `scientific_observations`）。只有 `reward=0` 或 `verify.log` 中的 `FAIL` 不足以归因。
5. **因果闭环**：修正所定位的智能体决策或操作后，可以直接避免本次最终失败。

> **启动前失败规则（`runtime.agent_started == false`）**：
> - 若存在致命基础设施故障证据（`causal_candidate == true`），归因 `infra`；缺少此类证据时，保留 `unknown`（`UNKNOWN_INSUFFICIENT_EVIDENCE`）。
> - 只要 `runtime.agent_started == false`，就不得将 `primary_root_cause.category` 判为 `"agent"`。

> **验证器未运行试次规则（`runtime.verifier_started == false` 或 `runtime.verdict in ("unknown", "not_run")`）**：
> - 验证器未启动时，单凭 `sig:premature_completion` 轨迹启发式信号不能作为智能体正面证据（`VERIFIER_NOT_RUN_PREMATURE_BLAME`）。归因 `agent` 需要在 `evidence_refs` 中引用非过早结束类的观测（如未恢复的科学报错、`agent_mismatch` 契约违约、`wrong_output_path` 等）。
> - Harbor 的已声明交付物来自 `task.toml` 中的 `artifacts`，而不是固定写死的 `results.json`。`premature_completion` 信号仅在某个*已声明*路径与采集到的 `<trial>/artifacts/app/...` 目录树核对后仍确认缺失时成立。
> - 发生在智能体执行之后、验证之前的运行器采集故障仍以 `infra` 为主根因；若运行器确实指明了缺失交付物，可列为伴随因素。若超时的因果链先于采集停止，以超时优先。验证器从未运行过时，该试次保持未评分状态。
> - 若确认存在远程交付中断（最后一次调度器查询显示作业仍在运行、智能体在无试次异常的情况下返回 `end_turn`，且公开契约和验证器均在采集目录中指认同一个缺失文件），可支持过早终止判断。

<a id="3-区分评测器缺陷与-agent-错误-distinguishing-verifier-defects-vs-agent-errors"></a>

## 3. 区分验证器缺陷与智能体错误

当 `verifier/verify.log` 输出报错或 `FAIL: <msg>` 时：

- **检查 1 — 验证器中断**：
  沿终态回执追到最早能证实的中断点。若证实为评分环境前置检查拦截（如缺少所需运行库或解释器），可支持基础设施候选；若发生在打包输入或参照夹具的预检阶段，可支持验证器依赖候选（`VERIFIER_DEPENDENCY_PREFLIGHT_FAILED`）；若为孤立的验证器内部异常，支持 `VERIFIER_RECOMPUTE_DEFECT`。若异常由提交内容畸形或不完整触发，先核对公开契约再判断上游责任。`RewardFileNotFoundError` 只记录评分文件未生成，可能出现在上述任意中断之后。

- **检查 2 — 直接因果绑定的解析或正则缺陷（`failure_binding == "direct"`）**：
  `verify.py` 中的静态解析风险（如未支持 Fortran `D+03` 指数或 namelist 中的 `/` 截断）仅在 `failure_binding == "direct"`（失败字段确实因该正则或解析器解析失败）时才能归因于 `verifier`。如果日志里仅在参考值中打印了 `1.23D+03`，而智能体实际写错了数值 `9.99E+02`，则 `failure_binding` 为 `"none"`，不得归咎于 `verifier`。

- **检查 3 — 隐式列序或格式假设（`VERIFIER_HIDDEN_CONTRACT`）**：
  如果 `verify.py` 不解析 `Step` 表头名称而直接按 `.split()` 固定下标取列，且 `instruction.md` 未规定列序，同时智能体的 `results.json` 已通过数值参考核验，归因为 `verifier`（`VERIFIER_HIDDEN_CONTRACT`）。

<a id="4-数值漂移与竞争假设规则-numerical-attribution--competing-hypothesis-rules"></a>

## 4. 数值分歧与竞争假设

- **三方闭环数值验证**：数值轨迹漂移判定需同时满足以下三项条件：
  1. **任务契约核验**：`instruction.md` 必须存在且未要求逐点完全重合的瞬时轨迹。若缺失 `instruction.md`，契约不完备，保守退回 `unknown`。
  2. **可执行 AST 代码检查**：`tests/verify.py` 必须在条件或数值比较表达式（如 `ast.Assert` 的 `node.test`、`ast.Compare` 的比较操作数或断言调用参数）中真正检查了瞬时轨迹或坐标。若相关词只出现在注释、报错提示字符串（`node.msg`）或包含判断（`"..." in log`）中，不构成验证器缺陷，保持保守弃判。
  3. **运行时直接因果绑定**：`verifier/verify.log` 必须把失败断言直接绑定到瞬时轨迹或坐标 RMSD，且同时记录有系综均值或守恒量吻合。非数值异常（如缺失文件或 `KeyError`）以及 pytest 测试函数名都不能误绑定为数值容差缺陷。
  4. 满足以上三项时，归因为 `verifier`（`VERIFIER_TOLERANCE_TOO_STRICT`）。
- **竞争假设必填**：每个失败或异常试次都必须生成 `competing_hypotheses`（`candidate-hypotheses.json`）及 `first_unrecovered_deviation`。
- **校准的证据强度评分**：置信度评分（`confidence_kind = "heuristic_evidence_score"`，`evidence_strength = "high" | "medium" | "low"`）由支持证据点数、多源交叉印证及竞争假设区分度计算得出（见 `scripts/confidence.py`）。

<a id="5-产物所有权清单与事务发布机制-artifact-ownership--transactional-publication"></a>

## 5. 产物所有权与发布

- **清单所有权与路径隔离**：输出目录中的 `.cfa_manifest.json` 记录 CFA 管理的文件与目录。写入前校验清单路径，拒绝目录穿越（`..`）、绝对路径以及超出输出根目录的路径。
- **保留用户自有文件**：没有有效清单时，CFA 认定自身不管理任何原有文件。用户自行创建的子目录与文件（如 `user_dir/report.md`、`custom_notes.txt`）都会保留，不作旧产物清理。
- **原子替换与进程互斥**：产物先写入目标目录中的隐藏临时文件，再通过 `os.replace` 原子替换就位，避免并发读取看到半写状态。同一输出目录的并发 CLI 写入通过进程锁串行执行，并清理异常退出遗留的临时文件。
- **尽力回滚**：新产物全部就位后再清理旧的受管文件。发布出错时尽力恢复已备份文件并删除新建文件，次生 I/O 错误记入日志而不掩盖主异常。
