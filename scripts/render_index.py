#!/usr/bin/env python3
"""
Failure Report Global Index Renderer (`scripts/render_index.py`)

Scans all task directories under `<bench_root>/failure-report/`, computes aggregate
root-cause distributions, and renders a clean, atomic-locked `<bench_root>/failure-report/INDEX.md`.

Guarantees:
- Full recomputation: no incremental in-place edits.
- Process-level advisory file lock to prevent concurrent write races.
- Atomic file replacement (`os.replace` via temporary file in the same directory).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path
from runtime_state import score_was_produced
from typing import Any, Dict, List, Optional
from urllib.parse import quote

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from staging import acquire_publish_lock


CATEGORY_TITLES = {
    "agent": "智能体决策与执行失误 (Agent Decisions)",
    "verifier": "评测验证与规则判定缺陷 (Verifier Defects)",
    "case": "基准题目与规格设计缺陷 (Case / Task Design)",
    "infra": "运行环境与系统设施缺陷 (Runtime / Infra)",
    "unknown": "未分类或信息缺失 (Unknown / Insufficient)",
}


def natural_sort_key(s: Any) -> tuple:
    """Split string into text and numeric tokens for natural order sorting."""
    parts = re.split(r"(\d+)", str(s or ""))
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _read_metadata(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_task_metadata(task_dir: Path) -> Optional[Dict[str, Any]]:
    """Read modern, migrated flat, and evidence-only task report directories."""
    if not task_dir.is_dir() or task_dir.is_symlink():
        return None

    def first_file(names):
        return next((name for name in names if (task_dir / name).is_file()), None)

    summary_path = first_file(
        ["data/summary.json", "summary.json", f"{task_dir.name}.summary.json"]
    )
    analysis_path = first_file(
        ["data/analysis.json", "analysis.json", f"{task_dir.name}.analysis.json"]
    )
    evidence_path = first_file(
        ["data/evidence.json", "evidence.json", f"{task_dir.name}.evidence.json"]
    )
    card = _read_metadata(task_dir / summary_path) if summary_path else {}
    analysis = _read_metadata(task_dir / analysis_path) if analysis_path else {}
    evidence = _read_metadata(task_dir / evidence_path) if evidence_path else {}
    if not card and not analysis and not evidence:
        return None
    payload = card or analysis
    runtime = evidence.get("runtime") or {}
    prc = payload.get("primary_root_cause") or {}
    verdict = payload.get("verdict")
    if not verdict:
        status = runtime.get("verification_status")
        verdict = status if status in ("passed", "failed", "pending", "errored") else "unknown"
    # Explicit unscored runtime data must override historical passed/failed labels.
    scored = score_was_produced(runtime) if runtime else score_was_produced(payload)
    report_file = first_file([f"{task_dir.name}.md", "report.md", "report.zh.md", "report.en.md"])
    summary_file = first_file(["summary.md", f"{task_dir.name}.summary.md"])
    prescription_file = first_file(["skill-prescription.md", f"{task_dir.name}.prescription.md"])
    deep_review_file = first_file(["deep-review.md"])
    return {
        "task_id": card.get("task_id") or task_dir.name,
        "job_name": Path(str(evidence.get("job_dir"))).name if evidence.get("job_dir") else "—",
        "trial_name": payload.get("trial_name") or evidence.get("trial_name"),
        "verdict": verdict,
        "scored": scored,
        "category": prc.get("category")
        or ("none" if verdict in ("passed", "pending") else "unknown"),
        "subtype": prc.get("subtype") or "—",
        "code": prc.get("code") or "—",
        "confidence": prc.get("confidence"),
        "deep_review_status": (
            (analysis.get("causal_assessment") or {}).get("deep_review") or {}
        ).get("status"),
        "failure_reason": payload.get("failure_reason")
        or ((analysis.get("causal_assessment") or {}).get("deep_review") or {}).get("mechanism")
        or prc.get("summary")
        or "仅采集证据，尚未审查归因 / Collected evidence; attribution not reviewed",
        "has_data_dir": (task_dir / "data").is_dir(),
        "evidence_file": evidence_path,
        "analysis_file": analysis_path,
        "has_report": report_file is not None,
        "report_file": report_file,
        "has_summary": summary_file is not None,
        "summary_file": summary_file,
        "has_prescription": prescription_file is not None,
        "prescription_file": prescription_file,
        "deep_review_file": deep_review_file,
    }


def render_index_markdown(cases: List[Dict[str, Any]], updated_time: Optional[str] = None) -> str:
    """Render the full index markdown text."""
    cases_sorted = sorted(
        cases,
        key=lambda c: (
            natural_sort_key(c.get("job_name", "")),
            natural_sort_key(c.get("task_id", "")),
        ),
    )
    total = len(cases_sorted)
    passed = sum(1 for c in cases_sorted if c.get("verdict") == "passed" and score_was_produced(c))
    failed = sum(1 for c in cases_sorted if c.get("verdict") == "failed" and score_was_produced(c))
    pending = sum(1 for c in cases_sorted if c.get("verdict") == "pending")
    errored_unscored = sum(
        1 for c in cases_sorted if c.get("verdict") != "pending" and not score_was_produced(c)
    )
    scored = sum(score_was_produced(c) for c in cases_sorted)
    scored_pass_rate = f"{(passed / scored * 100):.1f}%" if scored > 0 else "0.0%"

    timestamp = updated_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = [
        "# 科学计算基准评测失败分析看板 (Failure Report Index)",
        "",
        (
            f"> **更新时间 / Updated**: `{timestamp}` · "
            f"**总用例 / Total**: `{total}` · "
            f"**通过 / Passed**: `{passed}` · "
            f"**已评分失败 / Failed**: `{failed}` · "
            f"**待复核 / Pending Review**: `{pending}` · "
            f"**未评分或异常 / Errored & Unscored**: `{errored_unscored}` · "
            f"**可评分通过率 / Scored Pass Rate**: `{scored_pass_rate}`"
        ),
        "",
        "## 1. 根因大类分布 (Category Distribution)",
        "",
        "| 大类 (Category) | 数量 (Count) | 占未通过比例 (Share of Non-Passed) |",
        "|---|---|---|",
    ]

    cat_counts: Dict[str, int] = {"agent": 0, "verifier": 0, "case": 0, "infra": 0, "unknown": 0}
    for c in cases_sorted:
        if c.get("verdict") in ("passed", "pending"):
            continue
        cat = c.get("category", "unknown")
        if cat not in cat_counts:
            cat = "unknown"
        cat_counts[cat] += 1

    non_passed = sum(cat_counts[c] for c in ("agent", "verifier", "case", "infra", "unknown"))

    for cat in ("agent", "verifier", "case", "infra", "unknown"):
        count = cat_counts.get(cat, 0)
        prop = f"{(count / non_passed * 100):.1f}%" if non_passed > 0 else "0.0%"
        lines.append(f"| `{cat}` {CATEGORY_TITLES.get(cat, cat)} | **{count}** | {prop} |")
    lines.append("")

    lines += [
        "## 2. 评测用例清单 (Case Manifest)",
        "",
        "| Job | Task ID | Verdict | Primary Category | Subtype / Code | Conf | Failure Reason | Links |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for c in cases_sorted:
        tid = c["task_id"]
        v = c.get("verdict", "unknown")
        cat = c.get("category", "unknown")
        sub = c.get("subtype", "—")
        code = c.get("code", "—")
        conf = c.get("confidence")

        # Visual strength indicator for confidence
        if v == "pending":
            conf_str = "—"
        elif v == "passed":
            conf_str = f"✅ {float(conf):.2f}" if isinstance(conf, (int, float)) else "✅ 1.00"
        elif isinstance(conf, (int, float)):
            c_val = float(conf)
            if c_val >= 0.65:
                indicator = "🟢"
            elif c_val >= 0.40:
                indicator = "🟡"
            else:
                indicator = "🔴"
            conf_str = f"{indicator} {c_val:.2f}"
        else:
            conf_str = "—"

        reason = " ".join(str(c.get("failure_reason") or "—").split())
        if len(reason) > 100:
            reason = reason[:97] + "..."
        reason = reason.replace("|", "\\|")

        sub_display = f"`{sub}`" if sub != "—" else "—"
        if code and code != "—":
            sub_display += f" (`{code}`)"

        # Links
        links = []
        directory = str(c.get("report_dir") or tid)

        def target(name, directory=directory):
            return quote(f"{directory}/{name}", safe="/._-")

        if c.get("has_summary"):
            sum_target = c.get("summary_file") or "summary.md"
            links.append(f"[速览]({target(sum_target)})")
        if c.get("has_report"):
            rep_target = c.get("report_file") or f"{tid}.md"
            links.append(f"[报告]({target(rep_target)})")
        if c.get("has_prescription"):
            rx_target = c.get("prescription_file") or "skill-prescription.md"
            links.append(f"[处方]({target(rx_target)})")
        if c.get("has_data_dir"):
            links.append(f"[数据]({target('data/')})")
        elif c.get("evidence_file"):
            links.append(f"[证据]({target(c['evidence_file'])})")
        if c.get("analysis_file") and not c.get("has_data_dir"):
            links.append(f"[归因数据]({target(c['analysis_file'])})")
        if c.get("deep_review_file"):
            links.append(f"[复核]({target(c['deep_review_file'])})")
        links_str = " · ".join(links) if links else "—"
        job = str(c.get("job_name") or "—").replace("|", "\\|")
        job_display = (
            f"[`{job}`]({quote(c['job_index'], safe='/._-')})" if c.get("job_index") else f"`{job}`"
        )

        lines.append(
            f"| {job_display} | **`{tid}`** | `{v}` | `{cat}` | {sub_display} | {conf_str} | {reason} | {links_str} |"
        )

    lines.append("")
    states = [r["deep_review_status"] for r in cases_sorted if r.get("deep_review_status")]
    if states:
        incomplete = sum(status not in {"traced", "open"} for status in states)
        lines.append(
            f"深查 {len(states)} · 已定位决策 {states.count('traced')} · 已审仍缺证据 {states.count('open')} · 未完成 {incomplete}"
        )
    return "\n".join(lines)


def _write_index(root: Path, content: str) -> Path:
    """Atomically replace a board while its report-directory lock is held."""
    target = root / "INDEX.md"
    fd, raw_path = tempfile.mkstemp(prefix=".index_tmp_", suffix=".md", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw_path, target)
    finally:
        if os.path.exists(raw_path):
            os.unlink(raw_path)
    return target


def update_failure_report_index(report_root: Path) -> Path:
    """Full atomic update of <report_root>/INDEX.md under process lock."""
    report_root = report_root.resolve()
    if not report_root.is_dir():
        report_root.mkdir(parents=True, exist_ok=True)

    with acquire_publish_lock(report_root):
        cases: List[Dict[str, Any]] = []
        jobs: Dict[Path, List[Dict[str, Any]]] = {}
        for entry in sorted(report_root.iterdir()):
            if not entry.is_dir() or entry.is_symlink() or entry.name.startswith("."):
                continue
            meta = _load_task_metadata(entry)
            if meta is not None:
                meta["report_dir"] = entry.relative_to(report_root).as_posix()
                cases.append(meta)
                continue
            # Exactly one job layer; never descend into task trial archives.
            for task in sorted(entry.iterdir()):
                if task.name.startswith(".") or task.name in ("data", "trials"):
                    continue
                meta = _load_task_metadata(task)
                if meta is None:
                    continue
                meta.update(
                    job_name=entry.name,
                    report_dir=task.relative_to(report_root).as_posix(),
                    job_index=(entry.relative_to(report_root) / "INDEX.md").as_posix(),
                )
                cases.append(meta)
                jobs.setdefault(entry, []).append(
                    {**meta, "report_dir": task.name, "job_index": None}
                )
        for job_root, job_cases in jobs.items():
            with acquire_publish_lock(job_root):
                _write_index(job_root, render_index_markdown(job_cases))

        md_content = render_index_markdown(cases)

        target_path = _write_index(report_root, md_content)

    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Render atomic failure-report INDEX.md")
    parser.add_argument(
        "--report-root",
        required=True,
        type=Path,
        help="Path to failure-report directory (e.g. <bench>/failure-report)",
    )
    args = parser.parse_args()

    index_file = update_failure_report_index(args.report_root)
    print(f"Updated index at: {index_file}")


if __name__ == "__main__":
    main()
