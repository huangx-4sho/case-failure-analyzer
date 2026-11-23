# 已有证据的核验与归因

**[English](../en/evidence-review.md) | [简体中文](evidence-review.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

**本页：** [缺失产物](#缺失产物) · [测评验证记录](#测评验证记录) · [契约与回放](#契约与回放) · [因果审查](#因果审查) · [调查具体决策](#调查具体决策) · [复核汇总](#复核汇总) · [能力判断](#能力判断) · [跨-harness-支持](#跨-harness-支持)

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
- 验证器给出的差值是偏差。只有当实际提交值能够绑定到包含验证器所指名全部标识的唯一结构化数据行时，才填写对应提交值；找不到唯一数据行时保留未定。
- `verification_coverage` 是验证器对自身覆盖范围的声明；缺少过程细节不能当作已独立复算。
- 已保存的比较可以支持其覆盖范围内的结果判断，但不能单独证明哪个科学决策导致了偏差。

本流程不追加计算。材料不足时，说明还需要找回哪些已有文件、检查点、任务输入或验证器消息。

用 `runtime.score_produced` 判断试次是否已产出最终分数，并保留 `reward_metrics` 中的原始名称。完整性得分可以与待定科学复核并存；仅做提交检查的项只记录门禁结论，有效的最终通过结论也可能与智能体超时同时存在。

## 契约与回放

如果提交内容因文本、字段形状、枚举值、路径或派生标签被拒，应把实际提交物、公开要求和失败评分规则并排比对。`contract_review` 可标记语义误拒、未公开格式要求以及定义歧义，供后续复核。验证器在结构门禁停下时，其后的科学检查通常尚未执行。

判断采集文件是否缺失前，先核对 `execution_scope_findings` 中的隔离重放产物记录；得出缺少检查覆盖的结论前，先检查 `requirement_coverage_findings` 中的嵌套标签记录。涉及不同数学定义的物理量（如受力分量最大值与逐原子受力向量模长最大值），要核对实际运行的代码与公开定义。

针对重试或仓库已有改动的环境，从试次自己的 `lock.json` 和保存的验证器回执出发，比对实际使用的暂存或内嵌文件、预期摘要以及外层任务包副本。在把当前检出目录里的源码匹配当成因果证据之前，先确认运行时实际加载的版本。

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

没有依据的项保留未解决，不要为了填满表格补造理由或勾选确认。推理文字和最终回复不能代替执行证据；缺失文件、已经恢复的警告和泛化失败信息不能代替决策与结果的关联。

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

将 `runtime.reward_scope` 和 `reward_metrics` 一起读取。科学复核待定时，完整度分保留在原指标中，`runtime.reward` 为空。仅有提交检查结果的试次不进入科学评分通过率。

依赖预检失败时，从试次自己的锁文件和 verifier 记录追到评分进程实际加载的副本，对照当时的清单或摘要。外层任务目录和内嵌快照可能含有同名文件。当前检出中的匹配项可以定位排查方向，支持正式归因还需确认运行时版本。

审查计划会自动为 1 至 32 MiB 且已采集摘要的轨迹启用扩展指纹。其他大文件可用 `--hash-source <artifact-id>` 指定。生成计划和应用审查时都会分块核对 SHA-256。每个指定文件最多读取 32 MiB，合计最多 128 MiB。源文件变动或竞争原因尚未排除，都会限制结论等级。

### 调查具体决策

审计完成后运行 `cfa.py dossier <task-id> --output <dossier-dir>`，也可直接传入保存的审计目录。该命令为失败试次生成 `dossier.md`、`dossier.json` 和一份待填写的 `review-plan.json`；不指定 `--output` 并传入 `--raw` 时可在终端查看结构化 JSON。

阅读调查包时，从记录的评分拒绝与匹配的评分函数出发。对于归档打包的验证器，物理来源应引用归档产物，并记录成员路径与行号。将公开要求、实际采集的提交物和还原源码并排对照：字面源码重建只采信完整写入、无歧义的字符串编辑和简单带引号 heredoc，动态拼接与间接执行则保留实际版本未定。思维过程摘录只解释当时动机并保留自身定位指针，责任认定必须绑定到真实工具动作。

在审查计划中，用 `decision.event_ref` 锚定具体动作。智能体决策可选填 `decision.mechanism`，说明 `actual_logic`（实际实现）、`expected_logic`（应有逻辑）、`why_unrecovered`（未纠正原因）和 `minimal_fix`（最小修正）。沿用现有决策绑定补齐依据、输入、输出和拒绝引用，并可按[智能体机制列表](../../references/agent-mechanisms.md)填写经过核实的 `capability_dimension.mechanism_tag`。完成后用原审计 JSON 与填好的计划运行 `cfa.py review apply`。

遇到停在具体失败检查项上的题目契约歧义时，使用 `decision.contract_ambiguity`，填入 `public_source_ref`、`submitted_interpretation` 和 `scorer_interpretation`，并在依据中引用公开材料、绑定实际提交物和具名拒绝。当某项原因仍保留为候选时，`decision.follow_up` 接受 `action_en` 和 `action_zh`，记录下一步取证动作。

如果已保存的执行结果、公开要求和提交物能把偏差本身坐实到具体逻辑缺陷，可记为 `defect_verification: verified_in_trial`；`quantitative_sufficiency` 独立判定。仅凭已证实的缺陷尚不足以确认它解释了全部偏差；只有保存的验证器对照记录把该改动单独隔离出来，并显示跨过容差线时，才判为 `sufficient`，同时在 `decision.quantitative_evidence` 中引用失败检查项并附上说明。

确实需要查看私有参考实现时，加上 `--with-oracle` 提取参考代码片段，用于定位实现差异；归责判断仍以运行当时可见的公开要求与已保存执行记录为准。

## 复核汇总

运行 `cfa.py review summary --audit-root <audit> --review-root <reviews> --dossier-root <dossiers> --output <summary>`，生成逐试次总榜、复核队列与机制统计。有多个复核目录时可重复传入 `--review-root`。汇总器会用原始证据重新校验每份复核结果，历史审计也需与当前试次结果文件摘要和裁定保持一致；输出链接相对于总榜目录保存。

总榜的类别统计表把证据支持的主因（`review_required=false`）和复核候选（`review_required=true`）分列统计。运行机制线索单列在运行分诊节中，不混入归因表。机制复发统计采用证据支持的复核或已证实具体缺陷的复核（`defect_verification=verified_in_trial`），并在可获取任务定义路径时按独立任务定义去重计数，避免共享 `case_id` 的不同任务被合并。

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
