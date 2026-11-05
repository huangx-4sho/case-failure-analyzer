#!/usr/bin/env python3
"""
Verifier Check Statistics (`scripts/verify_check_stats.py`)

Parses raw verifier log text (e.g. `<trial>/verifier/verify.log`) and counts
check-marker lines to characterize whether a failure is localized to a single
check or widespread across the verifier's checks. Zero dependencies: stdlib
`re` only.

Detected check-line styles (anchored at line start, uppercase markers):

- `FAIL: <msg>` / `PASS: <msg>` — the repo's `check(cond, msg)` / `fail(msg)`
  helper style used by the benchmark tasks' `tests/verify.py` (see e.g.
  `ccb-run/tasks_l2/ase-custom-calculator/tests/verify.py`): every failed
  check prints one `FAIL: <msg>` line, and a fully passing verifier prints a
  single final `PASS: <case-name>` verdict line. Confirmed against the
  committed fixture `tests/fixtures/verifier_divergence/ase-custom-calculator/verifier/verify.log`
  (`FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)`) and
  the attribution protocol docs (`FAIL: <msg>`).
- `[PASS] <msg>` / `[FAIL] <msg>` — bracketed per-check progress style.
- `ok [N] [-] <msg>` / `not ok [N] [-] <msg>` — TAP style.

Fail-fast caveat: repo verifiers abort at the first failed check, so a real
failing log contains exactly one `FAIL:` line and no per-check PASS lines;
`total_checks` is therefore a *lower bound* of the checks the verifier ran.
`single_failure` additionally requires `total_checks >= 2`, which only logs
carrying per-check pass markers (bracket/TAP styles, or multiple `PASS:`
lines) can satisfy. A lone fail-fast `FAIL:` sets only
`counterfactual_candidate`, which is *not* localization and must not be
scored as one: it cannot tell "the only check that failed" from "the only
check that ran". Callers must independently bind a candidate to
metric-level evidence before treating it as counterfactual support.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# `check()`/`fail()` helper style: `FAIL: <msg>` / `PASS: <msg>`.
_COLON_CHECK_RE = re.compile(r"^[ \t]*(?P<status>FAIL|PASS):[ \t]*(?P<msg>.*)$")
# Bracketed per-check style: `[FAIL] <msg>` / `[PASS] <msg>`.
_BRACKET_CHECK_RE = re.compile(r"^[ \t]*\[(?P<status>FAIL|PASS)\][ \t]*(?P<msg>.*)$")
# TAP style: `not ok [N] [-] <msg>` / `ok [N] [-] <msg>`. `\b` keeps prose
# words like "okay" from matching.
_TAP_FAIL_RE = re.compile(r"^[ \t]*not[ \t]+ok\b[ \t]*(?:\d+)?[ \t]*(?:-[ \t]*)?(?P<msg>.*)$")
_TAP_PASS_RE = re.compile(r"^[ \t]*ok\b[ \t]*(?:\d+)?[ \t]*(?:-[ \t]*)?(?P<msg>.*)$")


def _classify_check_line(line: str) -> Optional[Tuple[bool, str]]:
    """Classify one log line as a passed/failed check.

    Returns `(is_pass, message)` for recognized check lines, else `None`.
    """
    match = _COLON_CHECK_RE.match(line)
    if match:
        return match.group("status") == "PASS", match.group("msg").strip()
    match = _BRACKET_CHECK_RE.match(line)
    if match:
        return match.group("status") == "PASS", match.group("msg").strip()
    match = _TAP_FAIL_RE.match(line)
    if match:
        return False, match.group("msg").strip()
    match = _TAP_PASS_RE.match(line)
    if match:
        return True, match.group("msg").strip()
    return None


def build_check_stats(verify_log_text: str) -> Dict[str, Any]:
    """Count check-marker lines in raw verifier log text.

    Returns a dict with:
      - `total_checks`: passed + failed marker lines found (a lower bound for
        fail-fast verifiers, whose passing checks print no marker);
      - `passed_checks` / `failed_checks`: marker line counts;
      - `failed_check_summaries`: message text of each failed check, in log
        order;
      - `single_failure`: exactly one failed check AND at least two check
        lines total;
      - `widespread_failure`: at least three failed checks AND failed >=
        passed;
      - `counterfactual_candidate`: exactly one failed check (the failed item
        could be the sole blocker; the gate layer combines this with
        metric-level evidence to decide localization);
      - `single_failure` and `widespread_failure` are mutually exclusive by
        construction and both `False` when neither condition holds.

    Empty text or text without any check-marker lines yields all-zero counts,
    an empty summary list, and all three booleans `False`.
    """
    passed = 0
    failed = 0
    failed_check_summaries: List[str] = []

    if verify_log_text:
        for line in verify_log_text.splitlines():
            verdict = _classify_check_line(line)
            if verdict is None:
                continue
            is_pass, message = verdict
            if is_pass:
                passed += 1
            else:
                failed += 1
                failed_check_summaries.append(message)

    total = passed + failed
    return {
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "failed_check_summaries": failed_check_summaries,
        "single_failure": failed == 1 and total >= 2,
        "widespread_failure": failed >= 3 and failed >= passed,
        "counterfactual_candidate": failed == 1,
    }
