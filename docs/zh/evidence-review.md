# 已有证据的核验与归因

**[English](../en/evidence-review.md) | [简体中文](evidence-review.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

**本页：** [缺失产物](#缺失产物) · [测评验证记录](#测评验证记录) · [因果审查](#因果审查) · [能力判断](#能力判断) · [跨-harness-支持](#跨-harness-支持)

本流程只读取测评已经保存的产物，不执行验证器、模拟、Shell 命令或 Docker。测评可能已经做过独立复算，应检查那次复算留下了什么及其覆盖范围，而不是重复执行，也不能假设所有检查都做过复算。

## 缺失产物

先查看 `evidence.deliverable_audit`，再解释文件缺失：

| 状态 | 已经确认的事实 | 下一步核对的既有材料 |
| --- | --- | --- |
| `present` | 声明路径存在于采集目录中 | 大小、内容、格式与验证结果；空目录仍可能缺少必要内容 |
| `not_found_in_collected_tree` | 映射明确，且完整的有界目录扫描未找到该路径 | 运行器采集记录、任务要求路径、实际写入与读回记录 |
| `collection_failed` | 已保存的运行器记录表明采集发生故障 | 故障涉及哪个服务或路径，验证器是否执行 |
| `unresolved` | 路径映射、声明、目录可用性、权限或扫描覆盖不足 | 找回已有清单、修正输入路径，或补齐缺失的保存记录 |

`not_found_in_collected_tree` 不等于“智能体没有生成”。轨迹中有写文件命令，也不证明文件进入了最终采集目录；两者冲突时要保留冲突。声明目录不自动规定其内部必须有哪些文件；没有服务映射依据时，service 产物保持无法判定。

`verified_missing_outputs` 只记录已有失败检查或验证器消息中，对完整声明路径的明确缺失描述。要求本身、相似文件名（如 `output.json.backup`）、声明目录内某个子文件的缺失，以及引述、否定和示例语句，都不能据此证明声明路径缺失。采集配置也不等于公开的任务要求；判断智能体责任还要检查任务说明。

默认最多扫描 1,000 个目录项、八层目录，每个 JSON 最多读取 65,536 字节，不跟随符号链接映射。扫描限额、不可读文件、解析失败、检查路径和空产物会保留在证据中。文件存在或解析成功不代表科学结果正确。

## 测评验证记录

`causal_assessment.existing_verification` 保留来源文件、失败检查、声明覆盖范围、覆盖限制和数值记录。验证器报告做过复算、与参考值比较、仅给出 `FAIL`，是不同证据：

- 保留报告值、实际交付值、复算或参考值、容差、来源位置，以及与执行和产物的对应关系。
- 验证器给出的差值是偏差，不是交付值或绝对参考值。
- `verification_coverage` 是验证器对自身覆盖范围的声明；缺少过程细节不能当作已独立复算。
- 已保存的比较可以支持其覆盖范围内的结果判断，但不能单独证明哪个科学决策导致了偏差。

本流程不追加计算。材料不足时，说明还需要找回哪些已有文件、检查点、任务输入或验证器消息。

## 因果审查

默认审计在原因不明时给出 `observed_failure`，规则选出解释时给出 `candidate_cause`；通过的试次为 `not_applicable`。独立的结构化审查可以达到 `evidence_supported_cause`，表示现有记录和审查推理支持本次解释，不代表因果关系得到绝对证明，也不代表做了新实验。

从任务 ID 报告生成待填写的审查记录：

```bash
SKILL_DIR="/path/to/skills/case-failure-analyzer"
REPORT_DIR="/path/to/benchmark/failure-report/example-job/task208"
python3 "$SKILL_DIR/scripts/cfa.py" review plan \
  --evidence "$REPORT_DIR/data/evidence.json" \
  --analysis "$REPORT_DIR/data/analysis.json" \
  --output /path/to/review-inputs/task208-review.json
```

阅读已有材料后，填写具体决策及类型、实际执行事件、有效输入和输出引用、适用要求、最终失败观测，以及它们之间的因果推理。逐项审查题目、运行环境、验证器、执行实现、测量或后处理、随机波动等替代解释。不能一边认定某个主因，一边又将其排除；科学方法或参数原因需要把实现错误作为竞争解释检查。

没有依据的项保留未解决，不要为了填满表格补造理由或勾选确认。推理文字和最终回复不能代替执行证据；缺失文件、已经恢复的警告和泛化失败信息不能代替必要的决策—结果关联。

把审查记录应用到一个新的独立输出目录：

```bash
python3 "$SKILL_DIR/scripts/cfa.py" review apply \
  --evidence "$REPORT_DIR/data/evidence.json" \
  --analysis "$REPORT_DIR/data/analysis.json" \
  --review /path/to/review-inputs/task208-review.json \
  --output /path/to/reviewed-cases/task208
```

输出包含 `evidence.json`、`original-analysis.json`、`analysis.json`、`review.json`、`report.zh.md` 和 `report.en.md`，原始输入不变。JSON 和源文件摘要检查用于发现材料变化；默认每个源文件最多读取 1 MiB，合计 8 MiB。引用的来源未能核验时，会限制结论升级。结构校验检查引用、绑定、审查一致性和归因规则，不能自动判断领域解释在科学上是否正确。

审查可选填 `proposed_attribution`，包含 `category`、`subtype`、规范归因代码 `code`，以及分别撰写的 `summary_en` 和 `summary_zh`。类别必须与 `decision.kind` 一致。验证器、题目和环境问题可以使用相应类型的已保存观测作为决策锚点；Agent 原因仍须绑定执行事件。各类审查都需要引用有效输入、输出、适用要求和终止观测，并逐项检查竞争解释。

输出保留原始分析快照，供校验器重建归因变更。审查不会修改原始测评结论或奖励；保留的数值置信度会明确标为**原规则证据评分**，不能解释为本次审查的可信程度。源文件检查覆盖实际引用的来源，引用轨迹事件时也检查轨迹文件；无关共享任务日志的增长不会让已结束试次的审查失效。未能核验的引用仍记为限制。

### 具体失败位置与自检证据

报告、速览卡和 `inspect --section errors` 分别列出运行中断和评分拒绝。若一条拒绝消息只能匹配到一处评分代码，报告会给出行号和能识别的判定条件。能从唯一的交付文件中读到对应字段时，也会列出提交值。完整度评分会注明适用范围。`contract_review.failure_locations` 保存逐项定位记录，报告采用的记录在 `causal_assessment.failure_detail` 中。

核对自检时，从 `causal_anchors.self_check_executions` 找到实际执行的命令、对应脚本版本和输出。逐项对照公开判据与终态拒绝，一个脚本可能通过接口检查，却在数值检查中失败。若生成程序先出错、自检随后漏检，应分别记录这两个决定。

较大的证据文件可以在 `review plan` 中用 `--hash-source art:trajectory_json` 指定。每个文件使用一次该选项，并提供已有采集摘要的 artifact ID。生成计划和应用审查时都会分块读取文件，核对采集时的 SHA-256。每个指定文件最多读取 32 MiB，指定文件合计最多 128 MiB，普通扫描另计。引用的文件缺少采集摘要或内容已变，或者竞争原因尚未排除，都会限制结论等级。

## 能力判断

对主因归为 Agent 的失败试次，在 `review plan` 生成的 JSON 中填写可选字段 `capability_dimension`。从[八个维度](taxonomy.md#agent-capability-dimensions)选一个，提供 `dimension`、`rationale_zh`、`rationale_en` 和 `evidence_refs`。引用受审决策的事件，以及其 `basis`、`outcome` 中的依据。理由写清具体做法、影响及相邻维度的区别。结论类错误可引用写出提交报告的工具调用。一次审查绑定一个决策。

证据不足时，填写 `{"status":"insufficient_evidence","rationale_zh":"…","rationale_en":"…"}`，具体说明缺什么。留空表示尚待标注。结果位于 `causal_assessment.capability.diagnosis`，状态区分待标注、证据不足、候选、本次审查支持。自动规则只产生候选；单次审查的支持程度受同一决策的因果审查等级约束。其他根因和部分旧报告没有这个字段。父字段 `capability.status` 单独记录可复用指导的提议状态。

跨任务审查特定维度时，在请求中加入 `dimension`。每份输入都须对同一维度有本次审查支持，仍需另外论证是否重复出现了同一失误机制。

单次失败即使经过审查，也不足以确认可复用的科学能力缺口。每个试次的 `skill_prescription` 保持空值，启发式建议仅作为疑似指导方案保存。

`cfa.py review capability --review <request.json> --output <capability-assessment.json>` 读取已有审查结果，单独输出限定范围的能力判断。请求使用 `schema_version: "cfa-capability-review-v1"`，提供 `reviewed_bundles`、`reviewer`、包含书面 `guidance` 的 `skill_prescription` 对象，以及下列 `generality_review` 项：

- `shared_mechanism`、`task_independence`：各自覆盖所有提供的案例；
- `reusable`、`nontrivial`、`no_verifier_workaround`、`no_answer_substitution`、`scope_limits`。

每项包含 `rationale` 和 `case_ids`。至少需要两个任务和来源内容均独立、且已有证据支持 agent 原因的审查结果。这只是最低证据约束，不是统计准确率保证；结论限定为 `supported_in_reviewed_cases`，不推广为模型普遍缺乏某项能力。

## 跨 harness 支持

调用 skill 的客户端与被分析的轨迹格式是两件事。能读取文件并运行 Python 的 agent 都可以遵循 `SKILL.md`；原生发现目录和调用语法由客户端决定。

轨迹识别覆盖 ATIF、OpenAI chat 消息、Anthropic 内容块及 Claude Code 封装、OpenAI Responses 工具条目、Codex rollout response item。`trajectory_normalization` 记录格式、覆盖程度、诊断和指针空间。转换后的 `/steps/...` 指向规整化步骤，不是原始 JSONL 行号。未知记录、截断、未匹配结果或缺少工具结果都标记为部分覆盖，不能解释成智能体没有执行。
