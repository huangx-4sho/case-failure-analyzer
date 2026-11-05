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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from report_style import subtype_label
from staging import acquire_publish_lock


CATEGORY_TITLES = {
    "agent": "智能体决策与执行失误 (Agent Decisions)",
    "verifier": "评测验证与规则判定缺陷 (Verifier Defects)",
    "case": "基准题目与规格设计缺陷 (Case / Task Design)",
    "infra": "运行环境与系统设施缺陷 (Runtime / Infra)",
    "unknown": "未分类或信息缺失 (Unknown / Insufficient)",
}


def _load_task_metadata(task_dir: Path) -> Optional[Dict[str, Any]]:
    """Load metadata for a task folder from summary.json or analysis.json."""
    if not task_dir.is_dir():
        return None

    # Search paths for summary card or analysis
    summary_candidates = [
        task_dir / "data" / "summary.json",
        task_dir / "summary.json",
        task_dir / f"{task_dir.name}.summary.json",
    ]
    for sc in summary_candidates:
        if sc.is_file():
            try:
                card = json.loads(sc.read_text(encoding="utf-8"))
                if isinstance(card, dict):
                    prc = card.get("primary_root_cause") or {}
                    return {
                        "task_id": card.get("task_id") or task_dir.name,
                        "verdict": card.get("verdict") or "unknown",
                        "category": prc.get("category") or "unknown",
                        "subtype": prc.get("subtype") or "—",
                        "code": prc.get("code") or "—",
                        "confidence": prc.get("confidence"),
                        "failure_reason": card.get("failure_reason") or "—",
                        "has_data_dir": (task_dir / "data").is_dir(),
                        "has_report": (task_dir / f"{task_dir.name}.md").is_file() or (task_dir / "report.md").is_file(),
                        "has_summary": (task_dir / "summary.md").is_file() or (task_dir / f"{task_dir.name}.summary.md").is_file(),
                    }
            except Exception:
                pass

    # Fallback: check analysis.json
    analysis_candidates = [
        task_dir / "data" / "analysis.json",
        task_dir / "analysis.json",
        task_dir / f"{task_dir.name}.analysis.json",
    ]
    for ac in analysis_candidates:
        if ac.is_file():
            try:
                an = json.loads(ac.read_text(encoding="utf-8"))
                if isinstance(an, dict):
                    prc = an.get("primary_root_cause") or {}
                    return {
                        "task_id": an.get("case_id") or task_dir.name,
                        "verdict": an.get("verdict") or "unknown",
                        "category": prc.get("category") or "unknown",
                        "subtype": prc.get("subtype") or "—",
                        "code": prc.get("code") or "—",
                        "confidence": prc.get("confidence"),
                        "failure_reason": prc.get("summary") or "—",
                        "has_data_dir": (task_dir / "data").is_dir(),
                        "has_report": (task_dir / f"{task_dir.name}.md").is_file() or (task_dir / "report.md").is_file(),
                        "has_summary": (task_dir / "summary.md").is_file(),
                    }
            except Exception:
                pass

    return None


def render_index_markdown(cases: List[Dict[str, Any]], updated_time: Optional[str] = None) -> str:
    """Render the full index markdown text."""
    cases_sorted = sorted(cases, key=lambda c: str(c.get("task_id", "")))
    total = len(cases_sorted)
    failed = sum(1 for c in cases_sorted if c.get("verdict") == "failed")
    passed = sum(1 for c in cases_sorted if c.get("verdict") == "passed")

    timestamp = updated_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = [
        "# 科学计算基准评测失败分析看板 (Failure Report Index)",
        "",
        f"> **更新时间 / Updated**: `{timestamp}` · **总用例 / Total**: `{total}` · **失败 / Failed**: `{failed}` · **通过 / Passed**: `{passed}`",
        "",
        "## 1. 根因大类分布 (Category Distribution)",
        "",
        "| 大类 (Category) | 数量 (Count) | 占比 (Proportion) |",
        "|---|---|---|",
    ]

    cat_counts: Dict[str, int] = {"agent": 0, "verifier": 0, "case": 0, "infra": 0, "unknown": 0}
    for c in cases_sorted:
        cat = c.get("category", "unknown")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    for cat in ("agent", "verifier", "case", "infra", "unknown"):
        count = cat_counts.get(cat, 0)
        prop = f"{(count / total * 100):.1f}%" if total > 0 else "0.0%"
        lines.append(f"| `{cat}` {CATEGORY_TITLES.get(cat, cat)} | **{count}** | {prop} |")
    lines.append("")

    lines += [
        "## 2. 评测用例清单 (Case Manifest)",
        "",
        "| Task ID | Verdict | Primary Category | Subtype / Code | Conf | Failure Reason | Links |",
        "|---|---|---|---|---|---|---|",
    ]

    for c in cases_sorted:
        tid = c["task_id"]
        v = c.get("verdict", "unknown")
        cat = c.get("category", "unknown")
        sub = c.get("subtype", "—")
        code = c.get("code", "—")
        conf = c.get("confidence")
        conf_str = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "—"
        reason = " ".join(str(c.get("failure_reason") or "—").split())
        if len(reason) > 100:
            reason = reason[:97] + "..."
        reason = reason.replace("|", "\\|")

        sub_display = f"`{sub}`" if sub != "—" else "—"
        if code and code != "—":
            sub_display += f" (`{code}`)"

        # Links
        links = []
        if c.get("has_summary"):
            links.append(f"[速览]({tid}/summary.md)")
        if c.get("has_report"):
            links.append(f"[报告]({tid}/{tid}.md)")
        if c.get("has_data_dir"):
            links.append(f"[数据]({tid}/data/)")
        links_str = " · ".join(links) if links else "—"

        lines.append(
            f"| **`{tid}`** | `{v}` | `{cat}` | {sub_display} | {conf_str} | {reason} | {links_str} |"
        )

    lines.append("")
    return "\n".join(lines)


def update_failure_report_index(report_root: Path) -> Path:
    """Full atomic update of <report_root>/INDEX.md under process lock."""
    report_root = report_root.resolve()
    if not report_root.is_dir():
        report_root.mkdir(parents=True, exist_ok=True)

    with acquire_publish_lock(report_root):
        cases: List[Dict[str, Any]] = []
        for entry in report_root.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            meta = _load_task_metadata(entry)
            if meta is not None:
                cases.append(meta)

        md_content = render_index_markdown(cases)

        # Write to temporary file in the same directory for atomic os.replace
        target_path = report_root / "INDEX.md"
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=".index_tmp_",
            suffix=".md",
            dir=str(report_root),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(md_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path_str, target_path)
        except Exception:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise

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
