"""Aggregate validated reviews and carry forward unresolved trials."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from attribution.capabilities import DIMENSIONS
from render_index import natural_sort_key
from review_cause import read
from runtime_state import score_was_produced
from staging import paths_overlap, publish_staging_to_output
from validate_analysis import validate_all

_CATEGORY_LABELS = {
    "agent": "Agent",
    "verifier": "评分器",
    "case": "任务",
    "infra": "基础设施",
    "unknown": "未定",
}
_QUEUES = {
    "contract": "契约与字段",
    "scientific": "数值、物理建模与算法",
    "provenance": "文献、表格与溯源",
    "runtime": "超时与 SDK 上限",
}
_HOST_PATH = re.compile(r"""(?:/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)[^\s'"`<>]*""")


def _portable_text(value):
    return _HOST_PATH.sub("[host path]", str(value or ""))


def _trial_key(evidence):
    trial = evidence.get("trial_dir")
    if not isinstance(trial, str) or not trial:
        raise ValueError("missing trial_dir")
    return str(Path(trial).resolve())


def _status(evidence, analysis):
    verdict = analysis["verdict"]
    runtime = evidence.get("runtime") or {}
    if verdict == "failed" and runtime.get("reward_scope") == "submission_checks_only":
        return "提交检查未通过"
    if verdict in ("passed", "failed") and score_was_produced(runtime):
        return "通过" if verdict == "passed" else "科学评分未通过"
    return {"pending": "待独立复核", "errored": "评测异常"}.get(verdict, "无科学评分")


def _result_hash(evidence):
    return next(
        (
            a.get("sha256")
            for a in evidence.get("artifacts") or []
            if a.get("artifact_id") == "art:trial_result"
        ),
        None,
    )


def _queue(evidence, analysis):
    """Find investigation subjects. Queue names never assign responsibility."""
    observations = evidence.get("error_observations") or []
    if any(
        e.get("causal_candidate")
        and e.get("code")
        in {
            "INFRA_AGENT_TIMEOUT",
            "INFRA_AGENT_OUTPUT_TOKEN_LIMIT",
            "INFRA_AGENT_AUTOCOMPACT_THRASHING",
        }
        for e in observations
    ):
        return "runtime"
    contract = evidence.get("contract_review") or {}
    checks = (evidence.get("verifier_report") or {}).get("failed_checks") or []
    details = " ".join(str(c.get("detail") or c.get("name") or "") for c in checks)
    if (
        contract.get("review_items")
        or any(
            loc.get("public_schema_comparison") or loc.get("key_set_difference")
            for loc in contract.get("failure_locations") or []
        )
        or re.search(
            r"\b(?:schema|field set|key set|required key|expected type|output path|missing deliverable)\b",
            details,
            re.I,
        )
    ):
        return "contract"
    if re.search(
        r"\b(?:provenance|quotation|source availability|source identity|cross-table|"
        r"source.data|source page|evidence hash|input hashes|source order|cited evidence)\b",
        details,
        re.I,
    ):
        return "provenance"
    return "scientific"


def _report(folder):
    for name in (f"{folder.name}.md", "report.zh.md", "report.md", "summary.md"):
        if (folder / name).is_file():
            return str(folder / name)
    return None


def _reason(analysis, *, reviewed):
    assessment = analysis.get("causal_assessment") or {}
    root = analysis.get("primary_root_cause") or {}
    decision = assessment.get("decision") or {}
    detail = assessment.get("failure_detail") or {}
    event = detail.get("scoring_event") or detail.get("runtime_event") or {}
    manifestation = analysis.get("failure_manifestation")
    if isinstance(manifestation, dict):
        manifestation = manifestation.get("summary")
    text = (
        (
            root.get("summary_zh") or decision.get("description_zh") or decision.get("description")
            if reviewed
            else None
        )
        or (root.get("summary") if reviewed else None)
        or (
            event.get("detail")
            or event.get("matched_text")
            or root.get("summary_zh")
            or root.get("summary")
            or manifestation
        )
    )
    return " ".join(_portable_text(text).split())[:420] or "—"


def _assessment_value(assessment, key):
    raw = assessment.get(key)
    if isinstance(raw, dict):
        return raw.get("status")
    return raw if isinstance(raw, str) else None


def _review_record(folder, analysis, current):
    assessment = analysis.get("causal_assessment") or {}
    diagnosis = (assessment.get("capability") or {}).get("diagnosis") or {}
    root = analysis.get("primary_root_cause") or {}
    decision = assessment.get("decision") or {}
    return {
        "path": str(folder),
        "report": _report(folder),
        "basis": "current_audit" if current else "validated_earlier_audit_same_trial",
        "level": assessment.get("level"),
        "category": root.get("category") or "unknown",
        "code": root.get("code"),
        "event_ref": decision.get("event_ref"),
        "reason": _reason(analysis, reviewed=True),
        "dimension": diagnosis.get("dimension"),
        "mechanism_tag": diagnosis.get("mechanism_tag"),
        "defect_verification": _assessment_value(assessment, "defect_verification"),
        "quantitative_sufficiency": _assessment_value(assessment, "quantitative_sufficiency"),
    }


def _rank_review(item):
    return (
        item["level"] == "evidence_supported_cause",
        item["defect_verification"] == "verified_in_trial",
        item["basis"] == "current_audit",
    )


def build_summary(audit_root, review_roots=(), dossier_root=None):
    """Count trials once. Reviews must validate against their saved sources."""
    audits = {}
    for path in sorted(audit_root.rglob("analysis.json")):
        evidence_path = path.with_name("evidence.json")
        if not evidence_path.is_file() or path.with_name("review.json").is_file():
            continue
        folder = path.parent.parent if path.parent.name == "data" else path.parent
        analysis, evidence = read(path), read(evidence_path)
        key = _trial_key(evidence)
        if key in audits:
            raise ValueError(f"duplicate audit for {key}")
        audits[key] = (folder, evidence, analysis)
    if not audits:
        raise ValueError(f"no single-trial audits found under {audit_root}")

    dossiers = {}
    if dossier_root is not None:
        by_trial = {}
        for path in sorted(dossier_root.rglob("dossier.json")):
            trial = read(path).get("trial_name")
            if trial:
                by_trial.setdefault(trial, []).append(path.parent)
        trial_counts = Counter(evidence.get("trial_name") for _, evidence, _ in audits.values())
        for key, (_, evidence, _) in audits.items():
            trial = evidence.get("trial_name")
            paths = by_trial.get(trial, [])
            dossier = paths[0] if len(paths) == 1 and trial_counts[trial] == 1 else None
            if dossier:
                dossiers[key] = str(dossier / "dossier.md")

    reviews, rejected = {}, []
    review_paths = {path for root in review_roots for path in root.rglob("review.json")}
    for path in sorted(review_paths):
        folder = path.parent
        if not path.with_name("evidence.json").is_file():
            continue
        try:
            evidence = read(folder / "evidence.json")
            key = _trial_key(evidence)
            if key not in audits:
                continue
            analysis = read(folder / "analysis.json")
            _, current, base = audits[key]
            if analysis.get("verdict") != base.get("verdict") or _status(
                evidence, analysis
            ) != _status(current, base):
                raise ValueError("review changes the audited benchmark outcome")
            exact = current == evidence
            if not exact and (
                _result_hash(evidence) is None or _result_hash(evidence) != _result_hash(current)
            ):
                raise ValueError("earlier review does not match the current trial result bytes")
            original = read(folder / "original-analysis.json")
            if exact and original != base:
                exact = False
            ok, errors = validate_all(evidence, analysis, review_root=folder)
            if not ok:
                raise ValueError("; ".join(errors))
            reviews.setdefault(key, []).append(_review_record(folder, analysis, exact))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            rejected.append({"path": str(folder), "reason": _portable_text(exc)[:650]})

    rows = []
    for key, (folder, evidence, analysis) in audits.items():
        options = sorted(reviews.get(key, []), key=_rank_review, reverse=True)
        selected = options[0] if options else None
        root = analysis.get("primary_root_cause") or {}
        best_level = (
            "已获证据支持"
            if selected and selected["level"] == "evidence_supported_cause"
            else "已证实具体缺陷"
            if selected and selected["defect_verification"] == "verified_in_trial"
            else "已复核，原因未定"
            if selected and selected["category"] == "unknown"
            else "人工候选"
            if selected
            else "尚无单案复核"
        )
        ranked_categories = (
            {r["category"] for r in options if _rank_review(r) == _rank_review(selected)}
            if selected
            else set()
        )
        resolved = selected and best_level == "已获证据支持" and len(ranked_categories) == 1
        base_category = root.get("category") or "unknown"
        # Direct setup/runner and verifier package errors already have typed receipts.
        direct_stop = not dossiers.get(key) and (
            base_category == "infra"
            or str(root.get("code") or "")
            in {
                "VERIFIER_DEPENDENCY_PREFLIGHT_FAILED",
                "VERIFIER_REFERENCE_LFS_POINTER",
                "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH",
            }
        )
        if analysis["verdict"] in ("passed", "pending"):
            best_level = "无需失败归因"
        elif direct_stop and not selected:
            best_level = "已定位运行或预检中断"
        row = {
            "job": Path(evidence.get("job_dir") or folder.parent).name,
            "task_id": folder.name,
            "case_id": str(evidence.get("case_id") or folder.name),
            "trial_name": evidence.get("trial_name"),
            "outcome": _status(evidence, analysis),
            "base_category": base_category,
            "attribution": best_level,
            "supported_category": selected["category"] if resolved else None,
            "reason": selected["reason"] if selected else _reason(analysis, reviewed=False),
            "audit": _report(folder),
            "dossier": dossiers.get(key),
            "queue": _queue(evidence, analysis) if key in dossiers and not resolved else None,
            "reviews": options,
            "defect_verification": (
                "verified_in_trial"
                if any(r["defect_verification"] == "verified_in_trial" for r in options)
                else selected["defect_verification"]
                if selected
                else None
            ),
            "quantitative_sufficiency": selected["quantitative_sufficiency"] if selected else None,
        }
        if len(ranked_categories) > 1:
            row["attribution"] = "同等级复核存在分歧"
        rows.append(row)
    rows.sort(key=lambda row: (natural_sort_key(row["job"]), natural_sort_key(row["task_id"])))

    diagnoses = {
        (row["case_id"], review["dimension"], review["mechanism_tag"])
        for row in rows
        for review in row["reviews"]
        if review["dimension"] in DIMENSIONS
        and (
            review["level"] == "evidence_supported_cause"
            or review["defect_verification"] == "verified_in_trial"
        )
    }
    mechanisms = Counter((dimension, tag or "未标具体机制") for _, dimension, tag in diagnoses)
    return {
        "schema_version": "cfa-review-summary-v1",
        "trials": len(rows),
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "attribution_counts": dict(Counter(row["attribution"] for row in rows)),
        "supported_categories": dict(
            Counter(row["supported_category"] for row in rows if row["supported_category"])
        ),
        "defect_verification_counts": dict(
            Counter(row["defect_verification"] for row in rows if row["defect_verification"])
        ),
        "quantitative_sufficiency_counts": dict(
            Counter(
                row["quantitative_sufficiency"] for row in rows if row["quantitative_sufficiency"]
            )
        ),
        "queues": dict(Counter(row["queue"] for row in rows if row["queue"])),
        "mechanisms": [
            {"dimension": dim, "mechanism_tag": tag, "independent_cases": count}
            for (dim, tag), count in sorted(
                mechanisms.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "rejected_reviews": rejected,
        "rows": rows,
    }


def render_summary(summary):
    def cell(text):
        return " ".join(str(text or "—").split()).replace("|", "\\|")

    def link(label, path):
        if not path:
            return ""
        return f"[{label}](<{path}>)"

    lines = ["# 全量归因与复核总表", "", f"共 {summary['trials']} 条试次。", ""]
    lines.append(
        "评测结果 · " + "，".join(f"{status} {n}" for status, n in summary["outcomes"].items())
    )
    lines.append(
        "复核进度 · "
        + "，".join(f"{status} {n}" for status, n in summary["attribution_counts"].items())
    )
    defect = summary["defect_verification_counts"].get("verified_in_trial", 0)
    quantity = summary["quantitative_sufficiency_counts"]
    if defect:
        lines.append(f"已证实具体缺陷 · {defect} 条试次。")
    if quantity:
        lines.append(
            f"已绑定定量充分证据 · {quantity.get('sufficient', 0)} 条。"
            f"定量充分性未验证 · {quantity.get('unverified_without_recompute', 0)} 条。"
        )
    lines.extend(
        ["", "## 证据支持的主因分布", "", "| 大类 | 试次数 | 占已支持归因 |", "|---|---:|---:|"]
    )
    total = sum(summary["supported_categories"].values())
    for key, label in _CATEGORY_LABELS.items():
        n = summary["supported_categories"].get(key, 0)
        lines.append(f"| {label} | {n} | {n / total:.1%} |" if total else f"| {label} | {n} | — |")
    lines.extend(["", "## 待复核队列", "", "| 队列 | 试次数 |", "|---|---:|"])
    lines.extend(f"| {label} | {summary['queues'].get(key, 0)} |" for key, label in _QUEUES.items())
    lines.extend(["", "## D1–D8 机制统计", "", "| 维度 | 机制 | 独立任务数 |", "|---|---|---:|"])
    for item in summary["mechanisms"]:
        number = list(DIMENSIONS).index(item["dimension"]) + 1
        dimension = f"D{number} {DIMENSIONS[item['dimension']][0]}"
        lines.append(
            f"| {dimension} | {cell(item['mechanism_tag'])} | {item['independent_cases']} |"
        )
    recurring = [
        item
        for item in summary["mechanisms"]
        if item["independent_cases"] > 1 and item["mechanism_tag"] != "未标具体机制"
    ]
    lines.extend(["", "## 跨任务复发的具体机制", ""])
    if recurring:
        lines.extend(
            f"- `{item['mechanism_tag']}` · {item['independent_cases']} 个独立任务"
            for item in recurring
        )
    else:
        lines.append("当前通过校验的复核中，暂无带相同具体标签的独立任务达到两例。")
    lines.extend(
        [
            "",
            "## 逐试次记录",
            "",
            "| 作业和任务 | 评测结果 | 原规则 | 复核状态 | 队列 | 具体原因或中断 | 证据 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in summary["rows"]:
        status = row["attribution"]
        if row["supported_category"]:
            status += f" · {_CATEGORY_LABELS[row['supported_category']]}"
        evidence = [link("审计", row["audit"]), link("调查包", row["dossier"])]
        evidence.extend(
            link(f"复核 {i}", review["report"]) for i, review in enumerate(row["reviews"], 1)
        )
        lines.append(
            f"| {cell(row['job'])} / {cell(row['task_id'])} | {cell(row['outcome'])} |"
            f" {cell(row['base_category'])} | {cell(status)} |"
            f" {cell(_QUEUES.get(row['queue']))} | {cell(row['reason'])} |"
            f" {' · '.join(x for x in evidence if x)} |"
        )
    if summary["rejected_reviews"]:
        lines.extend(["", "## 校验受阻的复核", ""])
        lines.extend(
            f"- {link(Path(item['path']).name, item['path'])} · {cell(item['reason'])}"
            for item in summary["rejected_reviews"]
        )
    return "\n".join(lines) + "\n"


def _relative_links(summary, output):
    """Keep the written table relocatable with its input and output directories."""

    def rel(record, fields):
        for key in fields:
            if record.get(key):
                record[key] = os.path.relpath(record[key], output)

    for row in summary["rows"]:
        rel(row, ("audit", "dossier"))
        for item in row["reviews"]:
            rel(item, ("path", "report"))
    for item in summary["rejected_reviews"]:
        rel(item, ("path",))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cfa review summary")
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, action="append", default=[])
    parser.add_argument("--dossier-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    roots = [args.audit_root, *args.review_root] + (
        [args.dossier_root] if args.dossier_root else []
    )
    output = args.output.resolve() if args.output else None
    try:
        if any(not path.is_dir() for path in roots):
            raise ValueError("every input root must be a directory")
        if output and any(paths_overlap(output, path) for path in roots):
            raise ValueError("review summary output overlaps an input root")
        summary = build_summary(
            args.audit_root.resolve(),
            [path.resolve() for path in args.review_root],
            args.dossier_root.resolve() if args.dossier_root else None,
        )
        _relative_links(summary, output or Path.cwd())
        markdown = render_summary(summary)
        if output is None:
            print(markdown, end="")
            return 0
        with TemporaryDirectory() as temporary:
            stage = Path(temporary)
            (stage / "review-summary.md").write_text(markdown, encoding="utf-8")
            (stage / "review-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            publish_staging_to_output(stage, output, False, manifest_key="review-summary")
        print(output / "review-summary.md")
        return 0
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
