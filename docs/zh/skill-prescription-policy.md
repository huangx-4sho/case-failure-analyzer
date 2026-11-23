# 技能处方策略

**[English](../en/skill-prescription-policy.md) | [简体中文](skill-prescription-policy.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="领域技能处方生成策略-skill-prescription-generation-policy"></a>

**本页：** [五项准入条件](#1-五项准入条件) · [修复建议的责任分流](#2-修复建议的责任分流) · [技能处方结构](#3-技能处方结构)

每个试次的 `analysis.json["skill_prescription"]` 保持空值。规则提出的指导只作为 `causal_assessment.capability` 下的疑似方案保存。独立、只读的跨案例审查可以在 `capability-assessment.json` 中形成限定范围的指导；它不会安装新技能或追加计算。

<a id="1-五项准入门槛-eligibility-gate"></a>

## 1. 五项准入条件

**仅当以下 5 项条件全部满足时**，才可进入跨案例能力审查：

1. **主根因为 Agent**：`primary_root_cause.category == "agent"`。
2. **非偶发性能力缺口**：该失败不是一次性拼写笔误、偶然的 Shell 命令失误或基础设施抖动。
3. **跨用例可复用性**：所缺失的领域知识适用于一类科学计算任务（例如 LAMMPS `read_restart` 续算规范、Quantum ESPRESSO `scf` -> `bands` 电荷密度复用规范、CP2K SCF 不收敛诊断与恢复策略）。
4. **可操作协议**：指导方案可以表达为确定性的决策树、执行前核查清单或诊断协议。
5. **不封装评测器缺陷**：处方不能教导智能体迎合错误的验证器正则表达式或伪造数据。

<a id="2-非准入情况的修复分流表-routing-table-for-non-eligible-cases"></a>

## 2. 修复建议的责任分流

任一准入条件不满足时，都不能确认可复用指导。每个试次的 `skill_prescription` 始终为空；修复建议按以下规则分流至 `recommended_actions`：

| 主根因类别 | `recommended_actions` 责任分流方向 | 是否可进入跨案例指导审查 |
|---|---|---|
| `case` | 修正题目说明、补充资产文件或调整 `task.toml` 规范 | **否** |
| `infra` | 修复运行器调度、Docker 镜像源、网络、超时配置或容器环境 | **否** |
| `verifier` | 修复 `tests/verify.py` 解析器、正则表达式或容差阈值并补充回归测试 | **否** |
| `unknown` | 输出补充取证核查清单 | **否** |
| `agent`（具备可复用科学或工程能力缺口） | 生成领域 Skill 处方与 Agent 策略改进建议 | **是** |

<a id="3-技能处方结构-prescription-schema"></a>

## 3. 技能处方结构

独立的 `review capability` 请求需要书面的 `skill_prescription.guidance` 和多个独立任务的证据审查，具体请求格式见[已有证据审查](evidence-review.md#能力判断)。后续将合格方案写成可复用技能时，可以参考以下内容结构：

```yaml
recommended_skill:
  name: "<kebab-case-技能名称>"
  trigger:
    - "<可观测报错症状或任务模式 1>"
    - "<可观测报错症状或任务模式 2>"
  capability_gap:
    - "<观测到的具体科学计算或工作流推理缺口>"
  required_guidance:
    - "<诊断/执行核查清单步骤 1>"
    - "<诊断/执行核查清单步骤 2>"
  anti_patterns:
    - "<Agent 实际犯下的错误及必须避免的反模式>"
  evidence_cases:
    - "<case_id>:<event_ref>"
```
