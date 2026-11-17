# 技能处方策略

**[English](../en/skill-prescription-policy.md) | [简体中文](skill-prescription-policy.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="领域技能处方生成策略-skill-prescription-generation-policy"></a>

**本页：** [五项准入条件](#1-五项准入条件) · [修复建议的责任分流](#2-修复建议的责任分流) · [技能处方结构](#3-技能处方结构)

`case-failure-analyzer` 会按条件生成 `skill-prescription.md`（并填充 `analysis.json["skill_prescription"]`）。为防止将脆弱的临时绕过手段或单题补丁污染到 Agent 的通用技能库中，必须严格执行以下准入策略。

<a id="1-五项准入门槛-eligibility-gate"></a>

## 1. 五项准入条件

**仅当以下 5 项条件全部满足时**，才允许生成 `skill-prescription.md`：

1. **主根因为 Agent (`Agent Root Cause`)**：`primary_root_cause.category == "agent"`。
2. **非偶发性能力缺口 (`Non-Trivial Capability Gap`)**：该失败并非一次性拼写笔误、偶然的 Shell 命令失误或基础设施抖动。
3. **跨用例可复用性 (`Cross-Case Reusability`)**：所缺失的领域知识适用于一整类科学计算任务（例如：LAMMPS `read_restart` 续算规范、Quantum ESPRESSO `scf` -> `bands` 电荷密度复用规范、CP2K SCF 不收敛诊断与恢复策略）。
4. **可操作协议 (`Actionable Protocol`)**：指导方案可以表达为确定性的决策树、执行前核查清单（Pre-flight Checklist）或诊断协议。
5. **严禁封装评测器缺陷 (`No Verifier Bug Encapsulation`)**：处方绝不能教导 Agent 去迎合错误的评测器正则表达式或伪造数据。

<a id="2-非准入情况的修复分流表-routing-table-for-non-eligible-cases"></a>

## 2. 修复建议的责任分流

当上述 5 项准入条件中任意一项不满足时，`skill_prescription` 必须为 `null`，且修复建议按如下规则分流至 `recommended_actions`：

| 主根因大类 (`Category`) | `recommended_actions` 责任分流方向 | 是否生成 `skill-prescription.md`？ |
|---|---|---|
| `case` | 修正 Prompt 说明、补充资产文件或调整 `task.toml` 规范 | **否** |
| `infra` | 修复 Runner 调度器、Docker 镜像源、网络、超时配置或容器环境 | **否** |
| `verifier` | 修复 `tests/verify.py` 解析器/正则表达式/容差阈值并补充回归测试 | **否** |
| `unknown` | 输出补充取证核查清单 | **否** |
| `agent`（具备可复用科学/工程能力缺口） | 生成领域 Skill 处方 + Agent 策略改进建议 | **是** |

<a id="3-技能处方结构-prescription-schema"></a>

## 3. 技能处方结构

当满足准入条件时，`analysis.json` 中的 `skill_prescription` 字段与 `skill-prescription.md` 必须包含以下结构：

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
