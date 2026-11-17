#!/usr/bin/env python3
"""
Phase 4: Prompt, Task, and Verifier Contract Auditor (`scripts/audit_contract.py`)

Builds the Three-Way Contract Matrix (`Prompt <-> Verifier <-> Agent Output`)
by extracting requirements from `instruction.md`, statically inspecting
`tests/verify.py` with Python `ast` + pattern checks, checking `environment/assets/`,
and correlating with `verifier/verify.log` and agent trajectory actions.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from diagnostics import WARNING, make_diagnostic


def extract_prompt_contract(
    instruction_text: str,
    task_dir: Optional[Path],
    instruction_exists: Optional[bool] = None,
) -> Dict[str, Any]:
    if instruction_exists is None:
        instruction_exists = bool(instruction_text.strip())
    # 1. Extract code-block JSON keys under "values" / "units", or top-level JSON block / inline key declarations
    json_keys: List[str] = []
    for m in re.finditer(r"```json\s*(.*?)```", instruction_text, re.DOTALL):
        block = m.group(1)
        val_block = re.search(r'"values"\s*:\s*\{(.*?)\}', block, re.DOTALL)
        target_block = val_block.group(1) if val_block else block
        for km in re.finditer(r'"([a-zA-Z0-9_]+)"\s*:', target_block):
            k = km.group(1)
            if k not in ("values", "units", "metadata") and k not in json_keys:
                json_keys.append(k)
    for km in re.finditer(
        r"(?:with\s+key[s]?|key[s]?|field[s]?|containing)\s+`([a-zA-Z0-9_]+)`",
        instruction_text,
        re.IGNORECASE,
    ):
        k = km.group(1)
        if "." not in k and k not in ("values", "units", "metadata") and k not in json_keys:
            json_keys.append(k)

    # 2. Extract mentioned input assets in `/workspace/assets/...` or `assets/...`
    asset_refs: List[str] = []
    for m in re.finditer(r"(?:/workspace/)?assets/([A-Za-z0-9_.\-/]+)", instruction_text):
        rel = m.group(1).rstrip("`.,;)")
        if rel and "." in Path(rel).name and rel not in asset_refs:
            asset_refs.append(rel)

    # Check if referenced assets exist in task_dir/environment/assets or Dockerfile
    missing_prompt_assets: List[str] = []
    if task_dir and task_dir.is_dir():
        assets_dir = task_dir / "environment" / "assets"
        dockerfile_text = (
            (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8", errors="replace")
            if (task_dir / "environment" / "Dockerfile").is_file()
            else ""
        )
        for aref in asset_refs:
            candidate = assets_dir / aref
            if not candidate.exists() and aref not in dockerfile_text:
                missing_prompt_assets.append(aref)

    # 3. Extract mentioned output filenames
    output_files: Set[str] = {"results.json"} if "results.json" in instruction_text else set()
    for m in re.finditer(
        r"`([A-Za-z0-9_.\-/]+\.(?:out|in|ener|xyz|dat|lammps|data|xml|json|csv))`",
        instruction_text,
    ):
        token = m.group(1).lstrip("/")
        if token.startswith("workspace/"):
            token = token[len("workspace/") :]
        if not token.startswith("assets/") and not token.startswith("opt/"):
            output_files.add(token)
    if "log.lammps" in instruction_text:
        output_files.add("log.lammps")

    # 4. Check whether `thermo_style` column order is explicitly specified
    specifies_thermo_columns = bool(
        re.search(r"thermo_style\s+custom\s+\S+", instruction_text, re.IGNORECASE)
    )

    # 5. Check whether prompt explicitly mandates pointwise exact trajectory reproduction
    if instruction_exists:
        specifies_pointwise_trajectory: Optional[bool] = bool(
            re.search(
                r"(?:exact\s+trajectory|exact\s+coordinates|step-by-step\s+trajectory|pointwise\s+trajectory|exact\s+position\s+at\s+each\s+step|instantaneous\s+coordinate\s+matching)",
                instruction_text,
                re.IGNORECASE,
            )
        )
    else:
        specifies_pointwise_trajectory = None

    return {
        "instruction_exists": instruction_exists,
        "json_keys": json_keys,
        "asset_refs": asset_refs,
        "missing_prompt_assets": missing_prompt_assets,
        "output_files": sorted(output_files),
        "specifies_thermo_columns": specifies_thermo_columns,
        "specifies_pointwise_trajectory": specifies_pointwise_trajectory,
    }


TRAJECTORY_KEYWORD_RE = re.compile(
    r"(?:trajectory_rmsd|instantaneous_position|coord(?:inate)?_rmsd)",
    re.IGNORECASE,
)


class VerifierASTVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.checked_files: List[str] = []
        self.checked_keys: List[str] = []
        self.regex_patterns: List[str] = []
        self.fail_messages: List[str] = []
        self.split_vars: Set[str] = set()
        self.has_positional_split_index: bool = False
        self.indexed_split_snippets: List[str] = []
        self.has_instantaneous_trajectory_assertion: bool = False

    def _add_checked_file(self, raw_path: str) -> None:
        cleaned = raw_path.strip()
        if not cleaned or cleaned.startswith(("/tmp/", "/tests/", "/opt/", "/proc/", "/sys/")):
            return
        if cleaned.startswith("/workspace/"):
            cleaned = cleaned[len("/workspace/") :]
        elif cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned in ("refs.json", "verify.py", "tests/refs.json", "tests/verify.py"):
            return
        if "." in Path(cleaned).name and cleaned not in self.checked_files:
            self.checked_files.append(cleaned)

    def _inspect_test_expression(self, expr: Optional[ast.AST]) -> None:
        """Inspect condition or compared expression for trajectory keywords, strictly excluding error messages."""
        if expr is None:
            return
        for child in ast.walk(expr):
            if isinstance(child, ast.Name) and TRAJECTORY_KEYWORD_RE.search(child.id):
                self.has_instantaneous_trajectory_assertion = True
            elif isinstance(child, ast.Attribute) and TRAJECTORY_KEYWORD_RE.search(child.attr):
                self.has_instantaneous_trajectory_assertion = True
            elif isinstance(child, ast.Subscript):
                slice_node = child.slice
                if hasattr(slice_node, "value") and not isinstance(slice_node, ast.Constant):
                    slice_node = slice_node.value
                if (
                    isinstance(slice_node, ast.Constant)
                    and isinstance(slice_node.value, str)
                    and TRAJECTORY_KEYWORD_RE.search(slice_node.value)
                ):
                    self.has_instantaneous_trajectory_assertion = True
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
                and TRAJECTORY_KEYWORD_RE.search(child.args[0].value)
            ):
                self.has_instantaneous_trajectory_assertion = True

    def visit_Assert(self, node: ast.Assert) -> Any:
        # Strictly inspect ONLY node.test!
        # node.msg (e.g. `assert output_exists, "trajectory_rmsd=0.45"`) is only an error message
        # and must never be treated as evaluating a numerical trajectory rule.
        self._inspect_test_expression(node.test)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> Any:
        # Exclude membership tests (e.g. `"trajectory_rmsd" in log_text`), which are text checks
        is_membership = any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        if not is_membership:
            self._inspect_test_expression(node.left)
            for comparator in node.comparators:
                self._inspect_test_expression(comparator)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if TRAJECTORY_KEYWORD_RE.search(func_name):
            self.has_instantaneous_trajectory_assertion = True
        elif func_name in (
            "assertEqual",
            "assertAlmostEqual",
            "assert_allclose",
            "assert_array_almost_equal",
            "assert_array_less",
            "assert_array_equal",
            "assert_array_almost_equal_nulp",
            "isclose",
            "allclose",
            "assertLess",
            "assertGreater",
            "assert_less",
            "assert_greater",
        ):
            # Inspect only the compared values (positional args 0 and 1), NEVER msg or err_msg keywords
            for arg in node.args[:2]:
                self._inspect_test_expression(arg)
        elif func_name in ("check", "assertTrue"):
            # Condition is the first argument
            if node.args:
                self._inspect_test_expression(node.args[0])

        if func_name == "join" and len(node.args) >= 2:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Name) and first_arg.id == "WORKSPACE":
                parts = [
                    a.value
                    for a in node.args[1:]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ]
                if parts:
                    self._add_checked_file("/".join(parts))

        if func_name in ("open", "Path") and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                self._add_checked_file(arg0.value)

        if func_name in ("compile", "search", "match", "findall", "finditer") and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                self.regex_patterns.append(arg0.value)

        if func_name in ("check", "fail") and node.args:
            msg_arg = node.args[-1]
            if isinstance(msg_arg, ast.Constant) and isinstance(msg_arg.value, str):
                self.fail_messages.append(msg_arg.value)

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        # Detect `<expr>.split()[<int>]` or `<split_var>[<int>]`
        is_int_index = isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)
        if is_int_index:
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "split"
            ):
                self.has_positional_split_index = True
                self.indexed_split_snippets.append(f"split()[{node.slice.value}]")
            elif isinstance(node.value, ast.Name) and node.value.id in self.split_vars:
                self.has_positional_split_index = True
                self.indexed_split_snippets.append(f"{node.value.id}[{node.slice.value}]")
        elif isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            key_cand = node.slice.value
            if (
                re.match(r"^[a-zA-Z0-9_]+$", key_cand)
                and key_cand
                not in ("values", "units", "metadata", "results", "PATH", "HOME", "PYTHONPATH")
                and key_cand not in self.checked_keys
            ):
                self.checked_keys.append(key_cand)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> Any:
        target_name = node.target.id if isinstance(node.target, ast.Name) else ""
        if isinstance(node.iter, (ast.Tuple, ast.List)):
            items = [
                elt.value
                for elt in node.iter.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if target_name in ("fname", "name", "f") and items:
                for it in items:
                    if it not in self.checked_files:
                        self.checked_files.append(it)
            elif target_name in ("key", "k") and items:
                for it in items:
                    if it not in self.checked_keys:
                        self.checked_keys.append(it)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "split"
        ):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.split_vars.add(t.id)
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in ("KEYS", "REQUIRED_KEYS"):
                if isinstance(node.value, (ast.Tuple, ast.List)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            if elt.value not in self.checked_keys:
                                self.checked_keys.append(elt.value)
        self.generic_visit(node)


def inspect_verifier_code(verify_py_path: Optional[Path]) -> Dict[str, Any]:
    if verify_py_path is None or not verify_py_path.is_file():
        return {
            "exists": False,
            "checked_files": [],
            "checked_keys": [],
            "regex_patterns": [],
            "hazards": [],
            "diagnostics": [],
        }

    code_text = verify_py_path.read_text(encoding="utf-8", errors="replace")
    visitor = VerifierASTVisitor()
    diagnostics: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(code_text)
        visitor.visit(tree)
    except Exception as exc:
        # Recovery is correct (text-pattern hazards below still run), but the
        # AST-derived hazards are now missing. Recording it matters because an
        # empty hazard list otherwise reads as "the verifier looks clean".
        diagnostics.append(
            make_diagnostic(
                stage="audit_contract.inspect_verifier_code",
                code="VERIFIER_AST_PARSE_FAILED",
                severity=WARNING,
                message="tests/verify.py could not be parsed; AST-derived parser hazards were not collected.",
                source_ref="task:tests/verify.py",
                error_type=type(exc).__name__,
                error_message=str(exc),
                impact=(
                    "Gate 3 sees no AST-derived parser hazard, so a genuine verifier "
                    "defect may go unattributed and the failure can fall through to "
                    "an agent attribution."
                ),
            )
        )

    hazards: List[Dict[str, str]] = []

    # Hazard 1: Namelist regex `r"&(\w+)\b(.*?)/"` truncating at first `/` inside string values
    if re.search(r'r["\']&(\(\\w\+\)|\\w\+)\\b\(\.\*\?\)/["\']', code_text):
        hazards.append(
            {
                "hazard_type": "VERIFIER_REGEX_OR_PARSER_DEFECT",
                "subtype": "namelist_slash_truncation",
                "affected_parser": r"&(\w+)\b(.*?)/",
                "description": (
                    "Verifier uses `re.finditer(r'&(\\w+)\\b(.*?)/', text, re.DOTALL)` to parse Fortran namelists; "
                    "any `/` inside quoted file paths (e.g. `pseudo_dir = '/workspace/assets'` or `outdir = './outdir'`) "
                    "prematurely terminates the namelist match and drops subsequent keys such as `outdir`."
                ),
            }
        )

    # Hazard 2: Scientific notation regex that omits Fortran `D`/`d` exponent (`1.23D+03`)
    for pat in visitor.regex_patterns:
        pat_without_digit_esc = pat.replace("\\d", "")
        if (
            "[-\\d.E+]+" in pat or "[-\\d.eE+]+" in pat
        ) and "d" not in pat_without_digit_esc.lower():
            hazards.append(
                {
                    "hazard_type": "VERIFIER_REGEX_OR_PARSER_DEFECT",
                    "subtype": "scientific_notation_missing_d_exponent",
                    "affected_parser": pat,
                    "description": f"Verifier regex `{pat}` parses floating-point numbers without Fortran `D/d` exponent support.",
                }
            )

    # Hazard 3: Generic AST detection of positional `.split()` column indexing without header map
    parses_header_map = bool(
        re.search(
            r"\.index\(\s*['\"](?:PotEng|pe|Step|Temp|Lx)['\"]\s*\)", code_text, re.IGNORECASE
        )
        or re.search(r"dict\(zip\(header", code_text, re.IGNORECASE)
    )
    if visitor.has_positional_split_index and not parses_header_map:
        snippets = ", ".join(visitor.indexed_split_snippets[:3])
        hazards.append(
            {
                "hazard_type": "VERIFIER_HIDDEN_CONTRACT",
                "subtype": "implicit_thermo_column_index",
                "affected_parser": snippets,
                "description": (
                    f"Verifier indexes positional `.split()` columns (`{snippets}`) "
                    "without mapping `Step ...` header column names."
                ),
            }
        )

    # Hazard 4: Verifier checks instantaneous trajectory RMSD / per-step coordinates rather than ensemble averages
    checks_instantaneous_trajectory = visitor.has_instantaneous_trajectory_assertion
    if checks_instantaneous_trajectory:
        hazards.append(
            {
                "hazard_type": "VERIFIER_TOLERANCE_DEFECT",
                "subtype": "instantaneous_trajectory_rmsd",
                "affected_parser": "trajectory_rmsd",
                "description": (
                    "Verifier evaluates instantaneous trajectory coordinates or RMSD deviation "
                    "rather than ensemble statistics."
                ),
            }
        )

    return {
        "exists": True,
        "checked_files": visitor.checked_files,
        "checked_keys": visitor.checked_keys,
        "regex_patterns": visitor.regex_patterns,
        "hazards": hazards,
        "checks_instantaneous_trajectory": checks_instantaneous_trajectory,
        "diagnostics": diagnostics,
    }


def is_causally_bound_numerical_failure(verify_log_text: str) -> bool:
    """Check whether failure lines or failure blocks in verifier log directly report
    an instantaneous trajectory/coordinate tolerance failure, strictly rejecting
    non-numerical failures like missing files, syntax errors, or unhandled exceptions.
    """
    lines = verify_log_text.splitlines()

    # Identify failure anchor indices (accounting for pytest E / > prefixes)
    failure_blocks: List[str] = []
    for i, raw_line in enumerate(lines):
        clean_line = re.sub(r"^[E>+]\s+", "", raw_line.strip()).strip()
        if re.search(
            r"^(?:FAIL\b|FAILED\b|(?:[\w.]+\.)?AssertionError\b)|(?:^assert\s+)",
            clean_line,
            re.IGNORECASE,
        ):
            # Extract anchor line plus up to 4 following context lines (the assertion block)
            block_lines = [clean_line]
            for next_line in lines[i + 1 : i + 5]:
                c_next = re.sub(r"^[E>+]\s+", "", next_line.strip()).strip()
                if not c_next:
                    continue
                if c_next.startswith("___") or c_next.startswith("==="):
                    break
                block_lines.append(c_next)
            failure_blocks.append(" \n ".join(block_lines))

    if not failure_blocks:
        return bool(
            re.search(
                r"(?:trajectory_rmsd|instantaneous_position|coord(?:inate)?_rmsd)\s*=\s*[\d.]+\s*>",
                verify_log_text,
                re.IGNORECASE,
            )
        )

    for block in failure_blocks:
        # If the block represents a non-numerical runtime or missing-file exception, skip it
        if re.search(
            r"\b(?:FileNotFoundError|ModuleNotFoundError|ImportError|KeyError|IndexError|NoSuchFile|No such file|not found|absent)\b",
            block,
            re.IGNORECASE,
        ):
            continue

        # Pytest format: "FAILED test_file.py::test_func - ErrorType: message"
        # When pytest test name contains 'trajectory', do not falsely treat test name as failure metric
        eval_text = block
        if " - " in block and block.upper().startswith("FAILED"):
            eval_text = block.split(" - ", 1)[1]

        # Must contain a trajectory/coordinate metric bound to an explicit comparison or tolerance statement
        if re.search(
            r"(?:trajectory_rmsd|instantaneous_position|coord(?:inate)?_rmsd)\s*=\s*[\d.]+\s*>|"
            r"assert.*?(?:trajectory_rmsd|coord(?:inate)?_rmsd|instantaneous_position).*?[<>=!]+|"
            r"FAIL.*?(?:trajectory_rmsd|instantaneous_position|coord(?:inate)?_rmsd).*?[<>=!]+|"
            r"(?:trajectory_rmsd|instantaneous_position|coord(?:inate)?_rmsd).*?(?:exceed|drift|diverg|tolerance|threshold)|"
            r"(?:calculated|value|rmsd).*?[\d.]+\s*.*?(?:threshold|tolerance|expected).*?[\d.]+",
            eval_text,
            re.IGNORECASE,
        ):
            return True

    return False


_NON_FINITE_TOKENS = frozenset(
    {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
)
_MAX_DELIVERABLE_AUDIT_FILES = 200
_MAX_DELIVERABLE_AUDIT_BYTES = 8_000_000
_MAX_DELIVERABLE_AUDIT_ROWS = 200_000
_MAX_DELIVERABLE_AUDIT_JSON_NODES = 20_000


def _is_non_finite_str(val: str) -> bool:
    s = val.strip()
    if not s:
        return False
    if s.lower() in _NON_FINITE_TOKENS:
        return True
    try:
        parsed = float(s)
        return not math.isfinite(parsed)
    except ValueError:
        return False


def inspect_delivered_artifacts(
    trial_dir: Optional[Path],
    checked_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Scan `<trial>/artifacts` for JSON/CSV/TSV integrity issues.

    Detects empty tables/JSON objects, NaN/Inf numeric values, and cross-checks
    `checked_keys` (when provided) against keys in `<trial>/artifacts/**/results.json`.
    """
    result: Dict[str, Any] = {
        "scanned_files": [],
        "empty_deliverables": [],
        "nan_or_inf_values": [],
        "results_json_files": [],
        "results_json_keys": [],
        "missing_checked_keys": [],
    }
    if trial_dir is None:
        return result
    artifacts_root = trial_dir / "artifacts"
    if not artifacts_root.is_dir():
        return result

    all_results_keys: Set[str] = set()
    scanned = 0

    for path in sorted(artifacts_root.rglob("*")):
        if scanned >= _MAX_DELIVERABLE_AUDIT_FILES:
            break
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in (".json", ".csv", ".tsv"):
            continue
        try:
            if path.stat().st_size > _MAX_DELIVERABLE_AUDIT_BYTES:
                continue
        except OSError:
            continue
        scanned += 1
        try:
            rel_path = path.relative_to(trial_dir).as_posix()
        except ValueError:
            rel_path = path.name
        result["scanned_files"].append(rel_path)

        if suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue

            if isinstance(data, (dict, list)) and len(data) == 0:
                result["empty_deliverables"].append(
                    {
                        "file": rel_path,
                        "kind": "empty_json",
                        "detail": "JSON deliverable is empty",
                    }
                )

            is_results_json = path.name == "results.json" and isinstance(data, dict)
            if is_results_json:
                result["results_json_files"].append(rel_path)

            stack: List[Tuple[Any, str]] = [(data, "$")]
            visited = 0
            while stack:
                node, json_path = stack.pop()
                visited += 1
                if visited > _MAX_DELIVERABLE_AUDIT_JSON_NODES:
                    break
                if isinstance(node, dict):
                    for k, v in node.items():
                        key_str = str(k)
                        if is_results_json:
                            all_results_keys.add(key_str)
                        stack.append((v, f"{json_path}.{key_str}"))
                elif isinstance(node, list):
                    for idx, v in enumerate(node):
                        stack.append((v, f"{json_path}[{idx}]"))
                elif isinstance(node, float) and not math.isfinite(node):
                    result["nan_or_inf_values"].append(
                        {"file": rel_path, "key": json_path, "value": str(node)}
                    )
                elif isinstance(node, str) and node.strip().lower() in _NON_FINITE_TOKENS:
                    result["nan_or_inf_values"].append(
                        {"file": rel_path, "key": json_path, "value": node.strip()}
                    )

        else:
            delimiter = "," if suffix == ".csv" else "\t"
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            if not lines:
                result["empty_deliverables"].append(
                    {
                        "file": rel_path,
                        "kind": "empty_table",
                        "detail": "CSV/TSV file has 0 data rows",
                    }
                )
                continue
            try:
                reader = csv.DictReader(lines, delimiter=delimiter)
                row_count = 0
                for row_idx, row in enumerate(reader):
                    if row_idx >= _MAX_DELIVERABLE_AUDIT_ROWS:
                        break
                    row_count += 1
                    for col_name, val_str in row.items():
                        if val_str is None:
                            continue
                        val_text = str(val_str).strip()
                        if _is_non_finite_str(val_text):
                            result["nan_or_inf_values"].append(
                                {
                                    "file": rel_path,
                                    "key": str(col_name).strip() if col_name else "unknown_column",
                                    "row": row_idx + 1,
                                    "value": val_text,
                                }
                            )
                if row_count == 0:
                    result["empty_deliverables"].append(
                        {
                            "file": rel_path,
                            "kind": "empty_table",
                            "detail": "CSV/TSV table has 0 data rows",
                        }
                    )
            except csv.Error:
                continue

    result["results_json_keys"] = sorted(all_results_keys)
    if checked_keys and result["results_json_files"]:
        for ck in checked_keys:
            if ck not in all_results_keys and ck not in result["missing_checked_keys"]:
                result["missing_checked_keys"].append(ck)

    return result


def audit_contract(
    task_dir: Optional[Path],
    trial_dir: Optional[Path],
    normalized_traj: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from runtime_state import resolve_verifier_script_path

    instruction_path = task_dir / "instruction.md" if task_dir else None
    instruction_exists = bool(instruction_path and instruction_path.is_file())
    instruction_text = (
        instruction_path.read_text(encoding="utf-8", errors="replace") if instruction_exists else ""
    )
    prompt_info = extract_prompt_contract(
        instruction_text, task_dir, instruction_exists=instruction_exists
    )
    verify_py_path = resolve_verifier_script_path(task_dir)
    verifier_info = inspect_verifier_code(verify_py_path)
    deliverable_diagnostics = inspect_delivered_artifacts(
        trial_dir,
        checked_keys=verifier_info.get("checked_keys"),
    )
    diagnostics: List[Dict[str, Any]] = list(verifier_info.get("diagnostics") or [])

    verify_log_text = ""
    if trial_dir:
        for rel_vlog in (
            "verifier/verify.log",
            "verifier/test-stdout.txt",
            "verifier/pytest.log",
            "verifier/verifier.log",
            "verify.log",
        ):
            vpath = trial_dir / rel_vlog
            if vpath.is_file():
                text_cand = vpath.read_text(encoding="utf-8", errors="replace").strip()
                if text_cand:
                    verify_log_text = text_cand
                    break

    contract_observations: List[Dict[str, Any]] = []
    verifier_observations: List[Dict[str, Any]] = []
    cid = 1

    # 1. Check missing assets referenced by Prompt
    for missing_asset in prompt_info["missing_prompt_assets"]:
        contract_observations.append(
            {
                "contract_id": f"contract:{cid}",
                "item": f"asset:{missing_asset}",
                "prompt_requirement": "provided_in_assets",
                "verifier_requirement": "required",
                "agent_output_status": "missing_in_task_environment",
                "alignment": "case_defect",
                "details": f"Prompt references asset `{missing_asset}`, which does not exist in `environment/assets/` or `Dockerfile`.",
                "source_refs": ["task:instruction.md", "task:environment/Dockerfile"],
            }
        )
        cid += 1

    # 2. Compare required files between Prompt and Verifier
    prompt_files = set(prompt_info["output_files"])
    for vfile in verifier_info["checked_files"]:
        in_prompt = (
            (not prompt_files)
            or (vfile in prompt_files)
            or any(vfile.endswith(pf) or pf.endswith(vfile) for pf in prompt_files)
        )
        missing_in_verify = bool(
            f"Missing: {vfile}" in verify_log_text
            or (
                vfile in verify_log_text
                and re.search(
                    r"(?:FileNotFoundError|No such file|not found|missing)",
                    verify_log_text,
                    re.IGNORECASE,
                )
            )
        )
        status = "missing_at_verify" if missing_in_verify else "present"
        if not in_prompt:
            alignment = (
                "verifier_hidden_requirement" if missing_in_verify else "implicit_consistent"
            )
        else:
            alignment = "agent_mismatch" if missing_in_verify else "consistent"

        contract_observations.append(
            {
                "contract_id": f"contract:{cid}",
                "item": f"file:{vfile}",
                "prompt_requirement": "required" if in_prompt else "not_explicitly_mentioned",
                "verifier_requirement": "required",
                "agent_output_status": status,
                "alignment": alignment,
                "details": (
                    f"File `{vfile}` required by verifier; prompt_declared={in_prompt}, agent_status={status}."
                ),
                "source_refs": ["task:instruction.md", "task:tests/verify.py"],
            }
        )
        cid += 1

    # 3. Compare JSON schema keys between Prompt and Verifier
    prompt_keys = set(prompt_info["json_keys"])
    for vkey in verifier_info["checked_keys"]:
        in_prompt = (not prompt_keys) or (vkey in prompt_keys)
        missing_key = bool(
            f"missing key: {vkey}" in verify_log_text
            or f"KeyError: '{vkey}'" in verify_log_text
            or f'KeyError: "{vkey}"' in verify_log_text
            or (
                vkey in verify_log_text
                and re.search(r"(?:KeyError|missing key)", verify_log_text, re.IGNORECASE)
            )
        )
        if not in_prompt:
            alignment = "verifier_schema_mismatch" if missing_key else "implicit_consistent"
        elif missing_key:
            alignment = "agent_mismatch"
        else:
            alignment = "consistent"
        contract_observations.append(
            {
                "contract_id": f"contract:{cid}",
                "item": f"results.json:values.{vkey}",
                "prompt_requirement": "required" if in_prompt else "absent_or_different_key",
                "verifier_requirement": "required",
                "agent_output_status": "missing" if missing_key else "present",
                "alignment": alignment,
                "details": f"JSON key `{vkey}` checked by verifier (in_prompt={in_prompt}).",
                "source_refs": ["task:instruction.md", "task:tests/verify.py"],
            }
        )
        cid += 1

    # 4. Record Verifier failure message, detect verifier internal crash, & correlate with hazards
    if verify_log_text:
        verifier_observations.append(
            {
                "obs_id": "ver:fail_log",
                "type": "verifier_fail_message",
                "summary": verify_log_text.splitlines()[0][:240],
                "matched_text": verify_log_text[:500],
                "source_file": "verifier/verify.log",
                "source_pointer": "L1",
            }
        )

        # Check if verifier crashed internally (e.g. missing verifier temp/ref file `/tmp/...`, `/tests/...`, or unhandled exception)
        internal_crash_match = re.search(
            r"(?:FileNotFoundError|OSError|IOError|KeyError|AttributeError|ImportError|RuntimeError)"
            r".*?(?:/tmp/|/tests/|refs\.json|verify\.py)",
            verify_log_text,
            re.DOTALL,
        ) or (
            "Traceback (most recent call last):" in verify_log_text
            and not any(f in verify_log_text for f in verifier_info["checked_files"])
        )
        if internal_crash_match:
            verifier_observations.append(
                {
                    "obs_id": "ver:internal_crash",
                    "type": "verifier_internal_crash",
                    "subtype": "recompute_defect",
                    "code": "VERIFIER_RECOMPUTE_DEFECT",
                    "summary": f"Verifier crashed due to internal file/runtime error: {verify_log_text.splitlines()[-1][:200]}",
                    "matched_text": verify_log_text[:400],
                    "hazard_detected": True,
                    "failure_binding": "direct",
                    "binding_evidence": ["ver:fail_log"],
                    "triggered": True,
                    "source_file": "verifier/verify.log",
                    "source_pointer": "L1",
                }
            )

    for h_idx, h in enumerate(verifier_info["hazards"], start=1):
        sub = h["subtype"]
        affected_parser = h.get("affected_parser")
        failure_binding = "none"
        binding_evidence: List[str] = []
        affected_input: Optional[str] = None
        triggered = False

        if sub == "namelist_slash_truncation" and (
            "outdir mismatch" in verify_log_text or "prefix mismatch" in verify_log_text
        ):
            triggered = True
            failure_binding = "direct"
            binding_evidence = ["ver:fail_log"]
            affected_input = "namelist_file"
            contract_observations.append(
                {
                    "contract_id": f"contract:{cid}",
                    "item": "namelist:outdir_parser",
                    "prompt_requirement": "reuse_scf_charge_density",
                    "verifier_requirement": "regex_namelist_parse",
                    "agent_output_status": "valid_namelist_with_slash_path",
                    "alignment": "verifier_defect",
                    "details": h["description"],
                    "source_refs": ["task:tests/verify.py", "verifier/verify.log"],
                }
            )
            cid += 1
        elif sub == "implicit_thermo_column_index" and (
            "!= log last line" in verify_log_text and not prompt_info["specifies_thermo_columns"]
        ):
            triggered = True
            failure_binding = "direct"
            binding_evidence = ["ver:fail_log"]
            affected_input = "log.lammps"
            contract_observations.append(
                {
                    "contract_id": f"contract:{cid}",
                    "item": "log.lammps:thermo_style_columns",
                    "prompt_requirement": "not_specified",
                    "verifier_requirement": "hardcoded_positional_split_column",
                    "agent_output_status": "ecoh_in_results_json_passed_ref_tolerance",
                    "alignment": "verifier_defect",
                    "details": (
                        "Agent's `results.json` numerical values passed reference tolerance (`refs.json`), "
                        "but `verify.py` indexed `.split()` positionally without mapping the `Step` header columns."
                    ),
                    "source_refs": [
                        "task:instruction.md",
                        "task:tests/verify.py",
                        "verifier/verify.log",
                    ],
                }
            )
            cid += 1
        elif sub == "scientific_notation_missing_d_exponent":
            # Causal binding check: Do NOT trigger if D+03 only appears as a reference value while agent wrote an E-exponent value!
            d_matches = list(re.finditer(r"[-+]?\d+\.\d+[Dd][+-]\d+", verify_log_text))
            has_parse_error_symptom = bool(
                re.search(
                    r"(?:could not parse|cannot parse|failed to parse|unparseable|ValueError|NoneType)",
                    verify_log_text,
                    re.IGNORECASE,
                )
            )
            # Also check if the regex prefix in `verify.py` directly precedes the D-exponent string in verify_log_text
            prefix_matched_d_string = False
            if affected_parser and d_matches:
                prefix_part = re.split(r"\(\[", affected_parser)[0]
                if prefix_part:
                    try:
                        if re.search(prefix_part + r"[-+]?\d+\.\d+[Dd][+-]\d+", verify_log_text):
                            prefix_matched_d_string = True
                    except re.error as exc:
                        # The verifier's own regex prefix is not re-usable as a pattern.
                        # Keeping `prefix_matched_d_string` False is the safe default
                        # (it withholds a `direct` binding rather than inventing one),
                        # but it means the hazard cannot be bound this way.
                        diagnostics.append(
                            make_diagnostic(
                                stage="audit_contract.bind_hazards",
                                code="VERIFIER_PREFIX_REGEX_FAILED",
                                severity=WARNING,
                                message="The verifier regex prefix could not be composed into a probe pattern.",
                                source_ref="task:tests/verify.py",
                                error_type=type(exc).__name__,
                                error_message=str(exc),
                                impact=(
                                    "A D-exponent parser hazard cannot be bound directly to the "
                                    "failure through this route, so Gate 3 may abstain."
                                ),
                                context={"affected_parser": affected_parser},
                            )
                        )

            # Exclude cases where verify.log explicitly says agent's parsed value (e.g. `9.99E+02`) != reference `1.23D+03`
            agent_wrote_different_e_value = bool(
                re.search(r"[-+]?\d+\.\d+[Ee][+-]\d+", verify_log_text)
                and not has_parse_error_symptom
            )

            if (
                d_matches
                and (has_parse_error_symptom or prefix_matched_d_string)
                and not agent_wrote_different_e_value
            ):
                triggered = True
                failure_binding = "direct"
                binding_evidence = ["ver:fail_log"]
                affected_input = d_matches[0].group(0)
            elif d_matches:
                failure_binding = "none"
        elif sub == "instantaneous_trajectory_rmsd":
            has_ensemble_match = bool(
                re.search(
                    r"(?:ensemble average matches|ensemble.*within tolerance|conserved.*matches)",
                    verify_log_text,
                    re.IGNORECASE,
                )
            )
            has_trajectory_metric = bool(
                re.search(
                    r"(?:trajectory_rmsd\s*=\s*[\d.]+|instantaneous_position.*?>\s*[\d.]+|coord(?:inate)?_rmsd\s*=\s*[\d.]+)",
                    verify_log_text,
                    re.IGNORECASE,
                )
            )
            bound_to_failure = is_causally_bound_numerical_failure(verify_log_text)

            instruction_exists = prompt_info.get("instruction_exists", False)
            if (
                instruction_exists
                and has_ensemble_match
                and has_trajectory_metric
                and bound_to_failure
                and (prompt_info.get("specifies_pointwise_trajectory") is False)
            ):
                triggered = True
                failure_binding = "direct"
                binding_evidence = ["ver:fail_log"]
                affected_input = "trajectory"
                contract_observations.append(
                    {
                        "contract_id": f"contract:{cid}",
                        "item": "trajectory:instantaneous_rmsd",
                        "prompt_requirement": "ensemble_consistency",
                        "verifier_requirement": "instantaneous_trajectory_rmsd",
                        "agent_output_status": "ensemble_matches_trajectory_diverges",
                        "alignment": "verifier_defect",
                        "details": (
                            "Verifier evaluated instantaneous trajectory/position divergence rather than ensemble averages, "
                            "despite physical/numerical chaos and matching ensemble statistics."
                        ),
                        "source_refs": [
                            "task:instruction.md",
                            "task:tests/verify.py",
                            "verifier/verify.log",
                        ],
                    }
                )
                cid += 1
            elif has_trajectory_metric:
                failure_binding = "none"

        verifier_observations.append(
            {
                "obs_id": f"ver:hazard:{h_idx}",
                "type": "parser_hazard"
                if "REGEX" in h["hazard_type"]
                else "implicit_column_assumption",
                "summary": h["description"],
                "matched_text": h["subtype"],
                "hazard_detected": True,
                "failure_binding": failure_binding,
                "binding_evidence": binding_evidence,
                "affected_input": affected_input,
                "affected_parser": affected_parser,
                "triggered": triggered,
                "source_file": "tests/verify.py",
                "source_pointer": "AST",
            }
        )

    # 5. Deliverable content integrity observations (only when verify_log_text confirms the failure)
    if verify_log_text:
        if deliverable_diagnostics["empty_deliverables"] and re.search(
            r"\b(?:empty|no rows|0 rows|zero rows|no data|EmptyDataError)\b",
            verify_log_text,
            re.IGNORECASE,
        ):
            for ed in deliverable_diagnostics["empty_deliverables"]:
                contract_observations.append(
                    {
                        "contract_id": f"contract:{cid}",
                        "item": f"deliverable:{ed['file']}",
                        "prompt_requirement": "non_empty_deliverable",
                        "verifier_requirement": "non_empty_deliverable",
                        "agent_output_status": "empty_deliverable",
                        "alignment": "agent_mismatch",
                        "details": (
                            f"Delivered file `{ed['file']}` is empty ({ed['detail']}), "
                            "confirmed by verifier log."
                        ),
                        "source_refs": [f"art:{ed['file']}", "verifier/verify.log"],
                    }
                )
                cid += 1

        if deliverable_diagnostics["nan_or_inf_values"] and re.search(
            r"\b(?:nan|inf|infinity|non-finite|not finite)\b",
            verify_log_text,
            re.IGNORECASE,
        ):
            seen_non_finite: Set[Tuple[str, str]] = set()
            for item in deliverable_diagnostics["nan_or_inf_values"]:
                pair = (item["file"], item["key"])
                if pair in seen_non_finite:
                    continue
                seen_non_finite.add(pair)
                contract_observations.append(
                    {
                        "contract_id": f"contract:{cid}",
                        "item": f"deliverable:{item['file']}:{item['key']}",
                        "prompt_requirement": "finite_numeric_values",
                        "verifier_requirement": "finite_numeric_values",
                        "agent_output_status": "non_finite_value",
                        "alignment": "agent_mismatch",
                        "details": (
                            f"Delivered file `{item['file']}` contains non-finite value "
                            f"`{item['value']}` at `{item['key']}`, confirmed by verifier log."
                        ),
                        "source_refs": [f"art:{item['file']}", "verifier/verify.log"],
                    }
                )
                cid += 1

        if deliverable_diagnostics["missing_checked_keys"] and re.search(
            r"(?:KeyError|missing (?:required )?(?:key|field|column)|schema mismatch)",
            verify_log_text,
            re.IGNORECASE,
        ):
            already_flagged = {
                c.get("item")
                for c in contract_observations
                if c.get("alignment") in ("agent_mismatch", "verifier_schema_mismatch")
            }
            prompt_keys = set(prompt_info["json_keys"])
            for mk in deliverable_diagnostics["missing_checked_keys"]:
                if f"results.json:values.{mk}" in already_flagged:
                    continue
                if mk not in verify_log_text and "results.json" not in verify_log_text:
                    continue
                in_prompt = (not prompt_keys) or (mk in prompt_keys)
                contract_observations.append(
                    {
                        "contract_id": f"contract:{cid}",
                        "item": f"results.json:values.{mk}",
                        "prompt_requirement": "required"
                        if in_prompt
                        else "absent_or_different_key",
                        "verifier_requirement": "required",
                        "agent_output_status": "missing_in_delivered_results_json",
                        "alignment": "agent_mismatch" if in_prompt else "verifier_schema_mismatch",
                        "details": (
                            f"Verifier-checked key `{mk}` is absent from delivered `results.json` "
                            "and confirmed by verifier schema/key failure."
                        ),
                        "source_refs": ["task:tests/verify.py", "verifier/verify.log"],
                    }
                )
                cid += 1

    return {
        "prompt_contract": prompt_info,
        "verifier_contract": verifier_info,
        "deliverable_diagnostics": deliverable_diagnostics,
        "contract_observations": contract_observations,
        "verifier_observations": verifier_observations,
        "diagnostics": diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit contract between Prompt, Verifier, and Agent output."
    )
    parser.add_argument(
        "--task", required=False, type=Path, default=None, help="Path to task directory"
    )
    parser.add_argument(
        "--trial-dir", required=False, type=Path, default=None, help="Path to trial directory"
    )
    parser.add_argument(
        "--trajectory-norm",
        required=False,
        type=Path,
        default=None,
        help="Normalized trajectory JSON",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    norm_traj = None
    if args.trajectory_norm and args.trajectory_norm.is_file():
        norm_traj = json.loads(args.trajectory_norm.read_text(encoding="utf-8", errors="replace"))

    res = audit_contract(task_dir=args.task, trial_dir=args.trial_dir, normalized_traj=norm_traj)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
