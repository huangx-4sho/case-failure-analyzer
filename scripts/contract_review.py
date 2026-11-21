"""Read-only, conservative review of public/output/verifier contract disagreements.

Only saved, field-bound failures produce suggestions. Suggestions do not establish
scientific equivalence or alter reward, verdict, or attribution. No verifier code
is imported, executed, evaluated, or replayed.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any
from attribution.failure_location import locate_failures
from verifier_source import VerifierSource, collect_verifier_sources

MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 8_000_000
MAX_FILES = 96
_FENCED_JSON = re.compile(r"\s*(?:\#{1,6}[^\r\n]*\r?\n\s*)?```json\s*\r?\n([\s\S]*?)\r?\n```\s*")


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _strings(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _walk_values(value: Any, pointer: str = "$"):
    yield pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_values(child, f"{pointer}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{pointer}[{index}]")


class _Review:
    def __init__(self, evidence: dict):
        self.evidence = evidence
        self.coverage = {
            "public_contract": [],
            "submitted_artifacts": [],
            "failure_artifacts": [],
            "replay_scope_artifacts": [],
        }
        self.diagnostics: list[dict] = []
        self.items: list[dict] = []
        self.total = 0
        self.public: list[tuple[Path, str]] = []
        self.submitted: list[tuple[Path, Any]] = []
        self.failures: list[tuple[Path, str]] = []
        self.code = ""
        self.tree = ast.Module(body=[], type_ignores=[])
        self.parents: dict[ast.AST, ast.AST] = {}
        self.verify_path: Path | None = None
        self.verifier_source: VerifierSource | None = None
        self.sites: list[tuple[ast.AST, ast.AST]] = []

    def read(self, path: Path, group: str) -> str | None:
        record = {"path": str(path), "status": "missing", "truncated": False}
        self.coverage[group].append(record)
        try:
            size = path.stat().st_size
            record["size_bytes"] = size
            if not path.is_file() or path.is_symlink():
                record["status"] = "not_regular_file"
                return None
            limit = 4 * MAX_FILE_BYTES if group == "submitted_artifacts" else MAX_FILE_BYTES
            if size > limit or self.total + size > MAX_TOTAL_BYTES:
                record["status"] = "budget_skipped"
                return None
            self.total += size
            text = path.read_text(encoding="utf-8", errors="replace")
            record["status"] = "read"
            return text
        except OSError as exc:
            record["error_type"] = type(exc).__name__
            return None

    def load(self):
        task = Path(self.evidence["task_dir"]) if self.evidence.get("task_dir") else None
        trial = Path(self.evidence["trial_dir"]) if self.evidence.get("trial_dir") else None
        if task:
            paths = [task / "instruction.md"]
            assets = task / "environment" / "assets"
            # Includes shared interfaces and method notes, even if referred to indirectly.
            if assets.is_dir():
                paths.extend(
                    sorted(
                        path
                        for path in assets.rglob("*")
                        if path.suffix == ".md"
                        or (
                            path.suffix in {".json", ".yaml", ".yml"}
                            and any(token in path.stem.lower() for token in ("schema", "contract"))
                        )
                    )
                )
            for path in paths[:MAX_FILES]:
                if not path.resolve().is_relative_to(task.resolve()):
                    continue
                text = self.read(path, "public_contract")
                if text is not None:
                    self.public.append((path, text))
            if len(paths) > MAX_FILES:
                self.diagnostics.append(
                    {"code": "PUBLIC_FILE_LIMIT", "omitted_count": len(paths) - MAX_FILES}
                )
        candidates = []
        for entry in self.evidence.get("artifacts", []):
            raw = entry.get("abs_path")
            if not raw:
                continue
            path = Path(raw)
            rel = entry.get("rel_path", "")
            if (
                entry.get("scope") == "trial"
                and rel.startswith("artifacts/")
                and path.suffix.lower() in {".json", ".csv", ".md"}
                and trial is not None
                and path.resolve().is_relative_to(trial.resolve() / "artifacts")
            ):
                candidates.append(path)
        for path in sorted(set(candidates), key=lambda p: (p.suffix.lower() == ".md", p))[
            :MAX_FILES
        ]:
            text = self.read(path, "submitted_artifacts")
            if text is None:
                continue
            try:
                if path.suffix.lower() == ".md":
                    fenced = _FENCED_JSON.fullmatch(text)
                    if not fenced and not text.lstrip().startswith("{"):
                        continue
                    value = json.loads(fenced[1] if fenced else text)
                elif path.suffix.lower() == ".json":
                    value = json.loads(text)
                else:
                    value = list(csv.DictReader(io.StringIO(text)))
                self.submitted.append((path, value))
            except (ValueError, csv.Error, RecursionError) as exc:
                self.diagnostics.append(
                    {
                        "code": "SUBMITTED_PARSE_FAILED",
                        "path": str(path),
                        "error_type": type(exc).__name__,
                    }
                )
        if len(set(candidates)) > MAX_FILES:
            self.diagnostics.append(
                {"code": "SUBMITTED_FILE_LIMIT", "omitted_count": len(set(candidates)) - MAX_FILES}
            )
        if trial:
            normalized = self.evidence.get("verifier_report") or {}
            names = dict.fromkeys(
                [
                    "verification.json",
                    "test-stdout.txt",
                    "verify.log",
                    *(
                        source["rel_path"]
                        for source in normalized.get("source_files") or []
                        if source.get("role") != "historical"
                    ),
                ]
            )
            for name in list(names)[:MAX_FILES]:
                path = trial / "verifier" / name
                if not path.resolve().is_relative_to(trial.resolve() / "verifier"):
                    continue
                text = self.read(path, "failure_artifacts")
                if text:
                    # JSON unescaping preserves actual failure message fragments.
                    try:
                        text = json.dumps(json.loads(text), ensure_ascii=False).replace("\\n", "\n")
                    except ValueError:
                        pass
                    self.failures.append((path, text))

    def use_source(self, task: Path, source: VerifierSource) -> None:
        self.verifier_source = source
        self.verify_path = task / source.path
        self.code = source.text
        self.tree = ast.parse(source.text)
        self.parents.clear()
        self.sites.clear()
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        failure_text = "\n".join(t for _, t in self.failures)
        for node in ast.walk(self.tree):
            parent = self.parents.get(node)
            message = None
            if isinstance(node, ast.Call) and node.args:
                fn = _name(node.func)
                if fn in {"require", "need", "ensure", "check"}:
                    message = node.args[-1]
                elif fn == "fail" or isinstance(parent, ast.Raise):
                    message = node.args[0]
            elif (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Tuple)
                and len(node.value.elts) == 2
            ):
                first, second = node.value.elts
                if isinstance(first, ast.Constant) and first.value is False:
                    message = second
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.List)
                and len(node.value.elts) == 1
            ):
                message = node.value.elts[0]
            if message is None:
                continue
            pieces = message.values if isinstance(message, ast.JoinedStr) else [message]
            literals = [
                n.value for n in pieces if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            pattern = "".join(
                re.escape(n.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                else r"[^\n]{1,200}?"
                for n in pieces
            )
            if sum(len(s.strip()) for s in literals) >= 18 and re.search(pattern, failure_text):
                guard = parent or node
                while guard in self.parents and not isinstance(guard, (ast.If, ast.ExceptHandler)):
                    guard = self.parents[guard]
                self.sites.append((node, guard))

    def source(self, node: ast.AST) -> dict:
        text = ast.get_source_segment(self.code, node) or ""
        return {
            "path": str(self.verify_path),
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "excerpt": text[:3000],
            "excerpt_truncated": len(text) > 3000,
            **(
                {
                    "archive_path": self.verifier_source.container,
                    "archive_member": self.verifier_source.member,
                    "source_sha256": self.verifier_source.sha256,
                }
                if self.verifier_source and self.verifier_source.container
                else {}
            ),
        }

    def doc_refs(self, terms: list[str]) -> list[dict]:
        refs = []
        for path, text in self.public:
            for line, content in enumerate(text.splitlines(), 1):
                if any(term.casefold() in content.casefold() for term in terms if term):
                    refs.append(
                        {
                            "path": str(path),
                            "line_start": line,
                            "excerpt": content[:1800],
                            "excerpt_truncated": len(content) > 1800,
                        }
                    )
        return refs[:20]

    def fields(self, field: str):
        for path, root in self.submitted:
            for pointer, value in _walk_values(root):
                if isinstance(value, dict) and field in value:
                    yield path, pointer + "." + field, value[field], value

    def scope(self, node: ast.AST) -> ast.AST:
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return self.tree

    def add(
        self,
        category: str,
        subtype: str,
        field: str,
        site,
        nodes: list[ast.AST],
        actual: list[dict],
        requirement: str,
        uncertainty: str,
        terms: list[str] | None = None,
    ):
        if not actual:
            return
        refs = self.doc_refs(terms or [field])
        public_complete = (
            bool(self.public)
            and all(r["status"] == "read" for r in self.coverage["public_contract"])
            and not any(d["code"] == "PUBLIC_FILE_LIMIT" for d in self.diagnostics)
        )
        self.items.append(
            {
                "review_id": f"contract_review:{len(self.items) + 1}",
                "category": category,
                "subtype": subtype,
                "field": field,
                "status": "review_required",
                "automatic_attribution_eligible": False,
                "summary": requirement,
                "summary_zh": {
                    "literal_prose_alias": f"评分器对 {field} 使用固定字面值比较；需核对说明性表达是否应按语义接受。",
                    "conditional_prose_token_parser": f"评分器按关键词解析 {field}；条件或否定说明可能被当成正面适用条件。",
                    "undocumented_nesting": f"评分器要求 {field} 嵌套，提交的相关叶字段位于顶层；需核对公开结构约定。",
                    "undocumented_enum": f"提交的 {field} 值不在评分器接受集合内；需复核公开枚举与等价表示。",
                    "undocumented_required_child": f"提交的 {field} 父对象已有说明内容，评分器另要求固定子键；需复核公开结构约定，并保留独立运行时主因。",
                    "undefined_spatial_center": f"{field} 的中心定义影响判定，需核对公开定义与评分公式。",
                    "undefined_relative_path_base": f"{field} 的相对路径基准影响文件定位，需核对公开约定与实际拼接。",
                    "undefined_overlap_full": f"{field} 的分类依赖包含方向，需核对 full/partial 的公开定义。",
                }.get(subtype, f"字段 {field} 的公开契约与评分规则需要复核。"),
                "verifier_requirement": requirement,
                "actual_submitted_representation": actual[:5],
                "public_requirement_evidence": refs,
                "public_requirement_coverage": {
                    "status": "bounded_corpus_read" if public_complete else "incomplete",
                    "absence_is_proven": False,
                    "interpretation": "Requirement not located in inspected public documents; semantic absence requires human review.",
                },
                "verifier_source_evidence": [
                    self.source(n) for n in dict.fromkeys([site[0], *nodes])
                ],
                "failure_binding": {
                    "status": "source_and_field_bound",
                    "source": self.source(site[0]),
                    "failure_artifacts": [
                        {
                            "path": str(p),
                            "excerpt": text[:1800],
                            "excerpt_truncated": len(text) > 1800,
                        }
                        for p, text in self.failures
                    ],
                },
                "uncertainty": uncertainty,
            }
        )

    def actual(self, field: str, predicate=lambda v: True) -> list[dict]:
        return [
            {"path": str(p), "pointer": ptr, "value": value}
            for p, ptr, value, _ in self.fields(field)
            if predicate(value)
        ]

    def review_group_list_shape(self):
        """Bind a rejected helper argument to the exact submitted group shape."""
        failure_text = "\n".join(text for _, text in self.failures)
        for site in self.sites:
            node, guard = site
            if not isinstance(guard, ast.If):
                continue
            scope = self.scope(node)
            if not isinstance(scope, ast.FunctionDef):
                continue
            list_args = {
                check.args[0].id
                for check in ast.walk(guard.test)
                if isinstance(check, ast.Call)
                and _name(check.func) == "isinstance"
                and isinstance(self.parents.get(check), ast.UnaryOp)
                and isinstance(self.parents[check].op, ast.Not)
                and len(check.args) == 2
                and isinstance(check.args[0], ast.Name)
                and isinstance(check.args[1], ast.Name)
                and check.args[1].id == "list"
            }
            for index, arg in enumerate(scope.args.args):
                if arg.arg not in list_args:
                    continue
                for call in ast.walk(self.tree):
                    if (
                        not isinstance(call, ast.Call)
                        or _name(call.func) != scope.name
                        or len(call.args) <= index
                    ):
                        continue
                    lookup = call.args[index]
                    if not (
                        isinstance(lookup, ast.Call)
                        and isinstance(lookup.func, ast.Attribute)
                        and lookup.func.attr == "get"
                        and isinstance(lookup.func.value, ast.Name)
                    ):
                        continue
                    receiver = lookup.func.value.id
                    caller = self.scope(call)
                    assignments = [
                        a
                        for a in ast.walk(caller)
                        if isinstance(a, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == receiver for t in a.targets)
                        and isinstance(a.value, ast.Call)
                        and isinstance(a.value.func, ast.Attribute)
                        and a.value.func.attr == "get"
                        and a.value.args
                        and isinstance(a.value.args[0], ast.Constant)
                        and isinstance(a.value.args[0].value, str)
                    ]
                    for assignment in assignments:
                        field = assignment.value.args[0].value
                        message_parts = (
                            _strings(node.value) if isinstance(node, ast.Return) else _strings(node)
                        )
                        suffix = next(
                            (
                                part
                                for part in message_parts
                                if part.startswith(":") and len(part) >= 18
                            ),
                            "",
                        )
                        if not suffix:
                            continue
                        groups = set(
                            re.findall(
                                re.escape(field) + r"\[([^]\n]+)\]" + re.escape(suffix),
                                failure_text,
                            )
                        )
                        for group in sorted(groups):
                            actual = []
                            nested_keys = set()
                            for path, pointer, mapping, _ in self.fields(field):
                                value = mapping.get(group) if isinstance(mapping, dict) else None
                                if not isinstance(value, dict):
                                    continue
                                lists = [
                                    {
                                        "key": key,
                                        "count": len(items),
                                        "record_fields": sorted(
                                            set().union(
                                                *(
                                                    set(item)
                                                    for item in items
                                                    if isinstance(item, dict)
                                                )
                                            )
                                        ),
                                    }
                                    for key, items in value.items()
                                    if isinstance(items, list)
                                    and items
                                    and all(
                                        isinstance(item, dict) and {"mol", "err"}.issubset(item)
                                        for item in items
                                    )
                                ]
                                if not lists:
                                    continue
                                nested_keys.update(item["key"] for item in lists)
                                actual.append(
                                    {
                                        "path": str(path),
                                        "pointer": f"{pointer}[{json.dumps(group)}]",
                                        "value_type": "object",
                                        "object_keys": list(value),
                                        "nested_sample_lists": lists,
                                        "value": value,
                                    }
                                )
                            if not actual:
                                continue
                            self.add(
                                "representation_shape_disagreement",
                                "group_object_vs_direct_list",
                                f"{field}[{group}]",
                                site,
                                [guard, assignment, call],
                                actual,
                                f"Verifier requires a direct list at {field}[{group}]; the submitted group is an object containing sample records under {', '.join(sorted(nested_keys))}.",
                                "Nested records are present, so this rejection does not establish missing molecular data. The public requirement's group/source layout and acceptance of nesting require review; sample identities, numerical correctness and later checks remain unverified.",
                                terms=[field, "per-molecule", "group"],
                            )
                            item = self.items[-1]
                            item["summary_zh"] = (
                                f"评分器要求 {field}[{group}] 直接为列表；提交的是对象，样本记录位于 {', '.join(sorted(nested_keys))} 子列表。"
                            )
                            item["uncertainty_zh"] = (
                                "嵌套样本记录确实存在，此拒绝不能单独证明分子数据缺失。公开要求如何组织分组与来源、是否接受嵌套仍需复核；样本身份、数值正确性与后续检查均未验证。"
                            )
                            item["expected_representation"] = {
                                "type": "list",
                                "access": ast.get_source_segment(self.code, lookup),
                            }

    def review(self):
        self.review_group_list_shape()
        for site in self.sites:
            call, guard = site
            scope = self.scope(call)
            scope_text = ast.get_source_segment(self.code, scope) or ""
            message = " ".join(_strings(call.args[0] if isinstance(call, ast.Call) else call.value))
            # Fixed child required inside an already populated descriptive object.
            # Only a negated .get() in the matched rejection guard qualifies.
            if isinstance(guard, ast.If):
                for negated in [
                    n
                    for n in ast.walk(guard.test)
                    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not)
                ]:
                    lookup = negated.operand
                    if not (
                        isinstance(lookup, ast.Call)
                        and isinstance(lookup.func, ast.Attribute)
                        and lookup.func.attr == "get"
                        and lookup.args
                        and isinstance(lookup.args[0], ast.Constant)
                        and isinstance(lookup.args[0].value, str)
                    ):
                        continue
                    child = lookup.args[0].value
                    parent = lookup.func.value
                    resolution = []
                    if isinstance(parent, ast.Name):
                        assignments = [
                            n
                            for n in ast.walk(scope)
                            if isinstance(n, ast.Assign)
                            and n.lineno < guard.lineno
                            and any(
                                isinstance(t, ast.Name) and t.id == parent.id for t in n.targets
                            )
                        ]
                        if assignments:
                            assignment = max(assignments, key=lambda n: n.lineno)
                            parent = assignment.value
                            resolution.append(assignment)
                    if not (
                        isinstance(parent, ast.Subscript)
                        and isinstance(parent.slice, ast.Constant)
                        and isinstance(parent.slice.value, str)
                    ):
                        continue
                    field = parent.slice.value
                    refs = self.doc_refs([field, child])
                    explicit = False
                    for _, public_text in self.public:
                        if re.search(rf"\b{re.escape(field)}\.{re.escape(child)}\b", public_text):
                            explicit = True
                        if re.search(
                            rf'["\']{re.escape(field)}["\']\s*:\s*\{{[^}}]{{0,2000}}["\']{re.escape(child)}["\']\s*:',
                            public_text,
                            re.S,
                        ):
                            explicit = True
                    for ref in refs:
                        line = ref["excerpt"]
                        if (
                            field in line
                            and child in line
                            and re.search(
                                r"required|must|include|contain|has|with|必须|包含|需要", line, re.I
                            )
                        ):
                            explicit = True
                    actual = self.actual(
                        field,
                        lambda value, child=child: (
                            isinstance(value, dict)
                            and bool(value)
                            and child not in value
                            and any(isinstance(v, str) and bool(v.strip()) for v in value.values())
                        ),
                    )
                    if actual and not explicit:
                        before = len(self.items)
                        self.add(
                            "hidden_format_requirement",
                            "undocumented_required_child",
                            f"{field}.{child}",
                            site,
                            [guard, parent, *resolution],
                            actual,
                            f"Verifier requires a nonempty {child!r} child under {field!r}; the submitted parent already contains descriptive entries.",
                            "The populated object is not proof of scientific completeness or equivalence to the required child. This secondary contract suggestion does not supersede an independent terminal runtime failure.",
                            [field, child],
                        )
                        if len(self.items) > before:
                            self.items[-1]["causal_role"] = "secondary_contract_review"
            # A caught nested JSON lookup fails while the same leaves exist flat.
            if isinstance(guard, ast.ExceptHandler):
                owner = self.parents.get(guard)
                if isinstance(owner, ast.Try):
                    for sub in ast.walk(owner):
                        if not isinstance(sub, ast.Subscript) or not isinstance(
                            sub.value, ast.Subscript
                        ):
                            continue
                        inner = sub.value
                        if not isinstance(inner.slice, ast.Constant) or not isinstance(
                            inner.slice.value, str
                        ):
                            continue
                        field = inner.slice.value
                        key_sets = [
                            n
                            for n in ast.walk(scope)
                            if isinstance(n, (ast.Tuple, ast.List))
                            and len(n.elts) >= 2
                            and all(
                                isinstance(e, ast.Constant) and isinstance(e.value, str)
                                for e in n.elts
                            )
                        ]
                        for keys in key_sets:
                            names = [e.value for e in keys.elts]
                            actual = []
                            for path, root in self.submitted:
                                if (
                                    isinstance(root, dict)
                                    and field not in root
                                    and all(k in root for k in names)
                                ):
                                    actual.append(
                                        {
                                            "path": str(path),
                                            "pointer": "$",
                                            "value": {k: root[k] for k in names},
                                        }
                                    )
                            if actual:
                                refs = self.doc_refs([field])
                                explicit = any(
                                    re.search(
                                        rf'["\']{re.escape(field)}["\']\s*:\s*\{{|(?:nested|object|under|within)\s+`?{re.escape(field)}',
                                        r["excerpt"],
                                        re.I,
                                    )
                                    for r in refs
                                )
                                if not explicit:
                                    self.add(
                                        "hidden_format_requirement",
                                        "undocumented_nesting",
                                        field,
                                        site,
                                        [inner, keys],
                                        actual,
                                        f"Verifier requires the reported leaves nested under {field!r}; submitted leaves exist at the root.",
                                        "Leaf values have not been numerically validated; accepting a different shape may expose independent failures.",
                                        names + [field],
                                    )
                                    break
            # Literal normalized-string equality on aliases in the failed validator.
            for compare in ast.walk(scope):
                if (
                    not isinstance(compare, ast.Compare)
                    or len(compare.ops) != 1
                    or not isinstance(compare.ops[0], ast.Eq)
                ):
                    continue
                literal = compare.comparators[0]
                if (
                    not isinstance(literal, ast.Constant)
                    or not isinstance(literal.value, str)
                    or not isinstance(compare.left, ast.Call)
                    or _name(compare.left.func) not in {"lower", "casefold"}
                ):
                    continue
                loop = compare
                while loop in self.parents and not isinstance(loop, ast.For):
                    loop = self.parents[loop]
                if not isinstance(loop, ast.For) or not isinstance(
                    loop.iter, (ast.Tuple, ast.List)
                ):
                    continue
                # The equality must feed an accumulator tested by this failed
                # guard; a different check in the same function is insufficient.
                receiver = self.parents.get(compare)
                while receiver in self.parents and not isinstance(receiver, ast.Call):
                    receiver = self.parents[receiver]
                guard_names = (
                    {n.id for n in ast.walk(guard.test) if isinstance(n, ast.Name)}
                    if isinstance(guard, ast.If)
                    else set()
                )
                if not (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Attribute)
                    and receiver.func.attr == "append"
                    and isinstance(receiver.func.value, ast.Name)
                    and receiver.func.value.id in guard_names
                ):
                    continue
                aliases = _strings(loop.iter)
                actual = []
                for alias in aliases:
                    actual.extend(
                        self.actual(
                            alias,
                            lambda v, expected=literal.value: (
                                isinstance(v, str)
                                and len(v.split()) >= 4
                                and v.strip().lower() != expected
                            ),
                        )
                    )
                refs = self.doc_refs(aliases)
                explicit_literal = any(
                    re.search(
                        rf"(?:exactly|literal(?:ly)?|must equal|must be|只能|必须为)\s*[`'\"]*{re.escape(literal.value)}\b",
                        r["excerpt"],
                        re.I,
                    )
                    for r in refs
                )
                if actual and not explicit_literal:
                    self.add(
                        "semantic_rejection",
                        "literal_prose_alias",
                        "/".join(aliases),
                        site,
                        [loop, compare],
                        actual,
                        f"Verifier treats a prose alias as true only when normalized text equals {literal.value!r}.",
                        "Natural-language equivalence is not established by this audit; review the submitted statement and any companion Boolean for consistency.",
                        aliases,
                    )
            # Enum branch bound to a rejected row (identifier appears in failure).
            if "unknown" in message.lower():
                chain = guard
                while isinstance(self.parents.get(chain), ast.If):
                    chain = self.parents[chain]
                comparisons = [n for n in ast.walk(chain) if isinstance(n, ast.Compare)]
                by_field: dict[str, list[tuple[ast.AST, str]]] = {}
                for comp in comparisons:
                    if (
                        not isinstance(comp.left, ast.Subscript)
                        or not isinstance(comp.left.slice, ast.Constant)
                        or not isinstance(comp.left.slice.value, str)
                    ):
                        continue
                    for value in comp.comparators:
                        for literal in _strings(value):
                            by_field.setdefault(comp.left.slice.value, []).append((comp, literal))
                failure_text = "\n".join(t for _, t in self.failures)
                for field, checks in by_field.items():
                    allowed = sorted(set(v for _, v in checks))
                    actual = []
                    for path, ptr, value, row in self.fields(field):
                        identifiers = [row.get(k) for k in ("id", "source_id", "name")]
                        if (
                            isinstance(value, str)
                            and value not in allowed
                            and any(isinstance(i, str) and i in failure_text for i in identifiers)
                        ):
                            actual.append(
                                {
                                    "path": str(path),
                                    "pointer": ptr,
                                    "value": value,
                                    "row_identifier": next(
                                        i
                                        for i in identifiers
                                        if isinstance(i, str) and i in failure_text
                                    ),
                                }
                            )
                    refs = self.doc_refs([field])
                    if actual and not all(
                        any(token in r["excerpt"] for r in refs) for token in allowed
                    ):
                        self.add(
                            "hidden_format_requirement",
                            "undocumented_enum",
                            field,
                            site,
                            [c for c, _ in checks],
                            actual,
                            f"Verifier accepts only the literal values {allowed!r} for {field}.",
                            "Alternative tokens are not presumed equivalent; inspect the retained file and provenance obligations separately.",
                        )
            self.review_definitions(site, scope, scope_text, message)
        # Remove repeated static sites found via duplicated enum/lookup AST nodes.
        self.items = list(
            {(i["category"], i["subtype"], i["field"]): i for i in self.items}.values()
        )
        for index, item in enumerate(self.items, 1):
            item["review_id"] = f"contract_review:{index}"

    def review_definitions(self, site, scope, scope_text, message):
        call, guard = site
        # Prose applicability parser: flag only a token-matching rejection that
        # reaches the observed failure, with actual conditional prose supplied.
        if isinstance(guard, ast.If):
            names = {n.id for n in ast.walk(guard.test) if isinstance(n, ast.Name)}
            for assign in ast.walk(scope):
                if not isinstance(assign, ast.Assign) or not any(
                    isinstance(t, ast.Name) and t.id in names for t in assign.targets
                ):
                    continue
                for gen in [n for n in ast.walk(assign.value) if isinstance(n, ast.GeneratorExp)]:
                    functions = {
                        _name(n.func) for n in ast.walk(gen.elt) if isinstance(n, ast.Call)
                    }
                    for fn in [
                        n
                        for n in ast.walk(scope)
                        if isinstance(n, ast.FunctionDef) and n.name in functions
                    ]:
                        searches = [
                            n
                            for n in ast.walk(fn)
                            if isinstance(n, ast.Call)
                            and _name(n.func) == "search"
                            and n.args
                            and isinstance(n.args[0], ast.Constant)
                        ]
                        if not searches or not isinstance(gen.generators[0].iter, ast.Name):
                            continue
                        field = gen.generators[0].iter.id
                        actual = self.actual(
                            field,
                            lambda v: (
                                isinstance(v, list)
                                and any(
                                    isinstance(x, str)
                                    and re.search(
                                        r"\b(?:because|for a|would require|unless|only when)\b",
                                        x,
                                        re.I,
                                    )
                                    for x in v
                                )
                            ),
                        )
                        if actual:
                            self.add(
                                "semantic_rejection",
                                "conditional_prose_token_parser",
                                field,
                                site,
                                [assign, fn],
                                actual,
                                "A failed condition gate delegates prose interpretation to regex token rules; supplied conditions include qualifications.",
                                "The audit does not establish that every scientific condition or the accompanying equation is correct. Inspect each regex against the complete conditional statement.",
                            )
        # Resolve only explicit, named helper implementations, never execute them.
        if message:
            for fn in [
                n
                for n in ast.walk(self.tree)
                if isinstance(n, ast.FunctionDef) and n.name in message
            ]:
                text = ast.get_source_segment(self.code, fn) or ""
                if "centers.append" in text and "sum(" in text and "/len(" in text.replace(" ", ""):
                    refs = self.doc_refs([fn.name, "center", "centre", "centroid"])
                    if not any(
                        re.search(
                            r"centroid|arithmetic mean|average.*coordinates|geometric center",
                            r["excerpt"],
                            re.I,
                        )
                        for r in refs
                    ):
                        self.add(
                            "definition_ambiguity",
                            "undefined_spatial_center",
                            fn.name,
                            site,
                            [fn],
                            self.actual(fn.name),
                            "Verifier defines each spatial family center as the unweighted mean of every participating atom coordinate.",
                            "Public center wording may admit other constructions; no alternative center is asserted mathematically equivalent, and numeric outputs are not recomputed.",
                            ["spatial_spread_angstrom", fn.name],
                        )
        failure_text = "\n".join(t for _, t in self.failures)
        numeric_guard = isinstance(guard, ast.If) and any(
            isinstance(n, ast.Call) and _name(n.func) in {"number", "float", "abs"}
            for n in ast.walk(guard.test)
        )
        if (
            "overlap" in failure_text
            and "category" in failure_text
            and "conclusion" in message
            and not numeric_guard
        ):
            for fn in [
                n
                for n in ast.walk(self.tree)
                if isinstance(n, ast.FunctionDef)
                and any(
                    isinstance(x, ast.Return)
                    and isinstance(x.value, ast.Constant)
                    and x.value.value == "full"
                    for x in ast.walk(n)
                )
            ]:
                text = ast.get_source_segment(self.code, fn) or ""
                if "min(" not in text:
                    continue
                keys = sorted(
                    {
                        s
                        for s in _strings(scope)
                        if "overlap_category" in s and s in "\n".join(t for _, t in self.failures)
                    }
                )
                for field in keys:
                    refs = self.doc_refs([field, "full"])
                    if not any(
                        re.search(
                            r"smaller|shorter|minimum|较小|较短|完全包含|完整覆盖",
                            r["excerpt"],
                            re.I,
                        )
                        for r in refs
                    ):
                        mappings = [
                            n
                            for n in ast.walk(scope)
                            if isinstance(n, ast.Dict)
                            and any(
                                isinstance(k, ast.Constant) and k.value == field for k in n.keys
                            )
                        ]
                        self.add(
                            "definition_ambiguity",
                            "undefined_overlap_full",
                            field,
                            site,
                            [fn, *mappings],
                            self.actual(field),
                            "Verifier labels overlap full when intersection width equals the smaller window width.",
                            "Full coverage of the paper window and containment of either window differ mathematically. This suggests an underspecified definition, not scientific equivalence.",
                            [field, "full"],
                        )
        # Filename base: bind actual path-valued CSV field to interpolated failure.
        if "unknown" in message.lower() and isinstance(guard, ast.If):
            failure_text = "\n".join(t for _, t in self.failures)
            for assign in ast.walk(scope):
                if (
                    not isinstance(assign, ast.Assign)
                    or not isinstance(assign.value, ast.Subscript)
                    or not isinstance(assign.value.slice, ast.Constant)
                ):
                    continue
                field = assign.value.slice.value
                if not isinstance(field, str) or not any(
                    word in field.lower() for word in ("file", "path")
                ):
                    continue
                assigned_names = {t.id for t in assign.targets if isinstance(t, ast.Name)}
                message_names = (
                    {n.id for n in ast.walk(call.args[0]) if isinstance(n, ast.Name)}
                    if isinstance(call, ast.Call)
                    else set()
                )
                if not assigned_names.intersection(message_names):
                    continue
                actual = self.actual(
                    field, lambda v: isinstance(v, str) and "/" in v and v in failure_text
                )
                refs = self.doc_refs([field])
                if actual and not any(
                    re.search(
                        r"basename|file ?name only|relative to|relative path.*(?:root|directory)|相对.*目录|仅.*文件名",
                        r["excerpt"],
                        re.I,
                    )
                    for r in refs
                ):
                    path_assignments = [
                        n
                        for n in ast.walk(scope)
                        if isinstance(n, ast.Assign)
                        and any(
                            isinstance(x, ast.Name) and x.id in assigned_names
                            for x in ast.walk(n.value)
                        )
                        and any(isinstance(x, ast.JoinedStr) for x in ast.walk(n.value))
                    ]
                    if not path_assignments:
                        continue
                    self.add(
                        "definition_ambiguity",
                        "undefined_relative_path_base",
                        field,
                        site,
                        [assign, *path_assignments, guard],
                        actual,
                        "Verifier rejects a submitted path-valued filename after composing its own input subdirectory prefix.",
                        "Resolve the intended base and verify the target exists before assigning causality. Other CSV representation errors may remain.",
                    )

    def requirement_coverage_review(self) -> list[dict]:
        """Report declared all-sample requirements separately from hidden format.

        Direct-key rejection is observable; scientific incompleteness is not
        inferred from key shape or paraphrased prose. Nested label occurrences
        and original text are retained for the reviewer.
        """
        constant_sets = {}
        for assignment in ast.walk(self.tree):
            if not isinstance(assignment, ast.Assign) or not isinstance(
                assignment.value, (ast.Tuple, ast.List, ast.Set)
            ):
                continue
            values = assignment.value.elts
            if len(values) < 2 or not all(
                isinstance(v, ast.Constant) and isinstance(v.value, str) for v in values
            ):
                continue
            for target in assignment.targets:
                if isinstance(target, ast.Name):
                    constant_sets[target.id] = (sorted({v.value for v in values}), assignment)
        findings = []
        for site in self.sites:
            failed, guard = site
            if not isinstance(guard, ast.If):
                continue
            grouped = {}
            for comparison in ast.walk(guard.test):
                if not (
                    isinstance(comparison, ast.Compare)
                    and len(comparison.ops) == 1
                    and isinstance(comparison.ops[0], ast.NotEq)
                ):
                    continue
                left, right = comparison.left, comparison.comparators[0]
                if not (
                    isinstance(left, ast.Call)
                    and _name(left.func) == "set"
                    and left.args
                    and isinstance(right, ast.Call)
                    and _name(right.func) == "set"
                    and right.args
                    and isinstance(right.args[0], ast.Name)
                    and right.args[0].id in constant_sets
                ):
                    continue
                parent = left.args[0]
                if not (
                    isinstance(parent, ast.Subscript)
                    and isinstance(parent.slice, ast.Constant)
                    and isinstance(parent.slice.value, str)
                ):
                    continue
                field = parent.slice.value
                labels, declaration = constant_sets[right.args[0].id]
                refs = self.doc_refs([field])
                scope_refs = [
                    r
                    for r in refs
                    if re.search(
                        r"\ball\b|\bevery\b|\beach\b|per[- ](?:sample|rate|condition)|所有|每个|全部",
                        r["excerpt"],
                        re.I,
                    )
                ]
                permissive = any(
                    re.search(r"aggregat(?:e|ed|ion)|汇总|聚合", r["excerpt"], re.I)
                    for r in scope_refs
                )
                # If public scope is absent or explicitly permits aggregation,
                # the exact-key guard cannot establish a coverage violation.
                if not scope_refs or permissive:
                    continue
                actual = []
                for path, pointer, value, _ in self.fields(field):
                    if not isinstance(value, dict):
                        continue
                    missing = sorted(set(labels) - set(value))
                    if not missing:
                        continue
                    nested = {
                        label: [
                            ptr
                            for ptr, node in _walk_values(value, pointer)
                            if isinstance(node, dict) and label in node
                        ]
                        for label in missing
                    }
                    quotes = [
                        {"pointer": f"{pointer}.{key}", "quote": child}
                        for key, child in value.items()
                        if isinstance(child, str)
                    ]
                    actual.append(
                        {
                            "path": str(path),
                            "pointer": pointer,
                            "submitted_keys": sorted(value),
                            "missing_direct_labels": missing,
                            "additional_direct_keys": sorted(set(value) - set(labels)),
                            "nested_label_occurrences": nested,
                            "submitted_text_quotes": quotes,
                            "value": value,
                        }
                    )
                if actual:
                    key = tuple(labels)
                    group = grouped.setdefault(
                        key,
                        {
                            "fields": [],
                            "actual": [],
                            "nodes": [failed, declaration],
                            "scope_refs": [],
                        },
                    )
                    group["fields"].append(field)
                    group["actual"].extend(actual)
                    group["nodes"].append(comparison)
                    group["scope_refs"].extend(scope_refs)
            for labels, group in grouped.items():
                label_refs = self.doc_refs(list(labels))
                public_labels = {
                    label: any(
                        re.search(rf"(?<![A-Za-z0-9_]){re.escape(label)}(?![A-Za-z0-9_])", text)
                        for _, text in self.public
                    )
                    for label in labels
                }
                fields = sorted(set(group["fields"]))
                explicit_key_examples = []
                for _, text in self.public:
                    for block in re.findall(r"```json\s*(.*?)```", text, re.S):
                        try:
                            parsed = json.loads(block)
                        except ValueError:
                            continue
                        for _, node in _walk_values(parsed):
                            if isinstance(node, dict):
                                explicit_key_examples.extend(
                                    field
                                    for field in fields
                                    if isinstance(node.get(field), dict)
                                    and set(labels).issubset(node[field])
                                )
                findings.append(
                    {
                        "category": "requirement_coverage",
                        "subtype": "per_sample_source_check_coverage",
                        "status": "review_required",
                        "automatic_attribution_eligible": False,
                        "fields": fields,
                        "required_labels": list(labels),
                        "summary": "A public all-sample reporting requirement is bound to a failed exact-label-set check; submitted direct keys do not cover the required label set.",
                        "actual_submitted_representation": group["actual"],
                        "public_requirement_evidence": group["scope_refs"] + label_refs,
                        "public_requirement_binding": {
                            "all_sample_scope_explicit": True,
                            "required_labels_public": public_labels,
                            "direct_key_examples_found_for_fields": sorted(
                                set(explicit_key_examples)
                            ),
                            "direct_key_schema_proven": False,
                        },
                        "verifier_source_evidence": [
                            self.source(n) for n in dict.fromkeys(group["nodes"])
                        ],
                        "failure_evidence": [
                            {
                                "path": str(p),
                                "excerpt": text[:1800],
                                "excerpt_truncated": len(text) > 1800,
                            }
                            for p, text in self.failures
                        ],
                        "uncertainty": "Exact direct-key coverage is established by the saved comparison, not scientific incompleteness. Nested label occurrences may represent aggregate evidence; public all-sample scope does not itself prescribe direct-key nesting. Submitted prose is quoted verbatim without inferring whether checking was complete. Missing public label definitions, incomplete source coverage, and alternate schemas require review; original attribution and verdict remain unchanged.",
                    }
                )
        return findings

    def execution_scope_review(self) -> list[dict]:
        """Locate collected reports omitted from an isolated replay's static inputs.

        This proves a scope discrepancy worth reviewing, not runtime causality:
        indirect writes and external imports cannot be resolved by this audit.
        """
        findings = []
        functions = {n.name: n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)}
        calls = {
            name: {_name(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
            for name, fn in functions.items()
        }

        def reaches(start, target, seen=None):
            seen = set() if seen is None else seen
            if start == target:
                return True
            if start in seen:
                return False
            seen.add(start)
            return any(
                reaches(name, target, seen) for name in calls.get(start, set()) if name in functions
            )

        artifacts = [
            a
            for a in self.evidence.get("artifacts", [])
            if a.get("scope") == "trial"
            and a.get("rel_path", "").startswith("artifacts/")
            and a.get("abs_path")
        ]
        for failed, _guard in self.sites:
            message_node = failed.args[0] if isinstance(failed, ast.Call) else failed.value
            message = " ".join(_strings(message_node))
            filenames = re.findall(
                r"\b([A-Za-z0-9_.-]+\.(?:md|json|csv|txt|tsv|yaml|yml))\b", message
            )
            if not re.search(r"required|missing|not found", message, re.I):
                continue
            failed_scope = self.scope(failed)
            if not isinstance(failed_scope, ast.FunctionDef):
                continue
            for filename in filenames:
                captured = [
                    a
                    for a in artifacts
                    if Path(a["abs_path"]).name == filename and Path(a["abs_path"]).is_file()
                ]
                if not captured:
                    continue
                for helper_name, helper in functions.items():
                    helper_calls = [n for n in ast.walk(helper) if isinstance(n, ast.Call)]
                    runs = [
                        n
                        for n in helper_calls
                        if _name(n.func) == "run"
                        and n.args
                        and isinstance(n.args[0], (ast.List, ast.Tuple))
                    ]
                    copies = [
                        n
                        for n in helper_calls
                        if _name(n.func) in {"copy", "copy2", "copyfile", "copytree"}
                    ]
                    if "replay" not in helper_name.lower() or not runs or not copies:
                        continue
                    entrypoints = sorted(
                        {
                            value
                            for run in runs
                            for value in _strings(run.args[0])
                            if value.endswith(".py")
                        }
                    )
                    for caller in functions.values():
                        assignments = [
                            n
                            for n in ast.walk(caller)
                            if isinstance(n, ast.Assign)
                            and isinstance(n.value, ast.Call)
                            and _name(n.value.func) == helper_name
                        ]
                        for assignment in assignments:
                            replay_names = {
                                n.id
                                for target in assignment.targets
                                for n in ast.walk(target)
                                if isinstance(n, ast.Name)
                            }
                            validations = [
                                n
                                for n in ast.walk(caller)
                                if isinstance(n, ast.Call)
                                and n.lineno > assignment.lineno
                                and _name(n.func) in functions
                                and reaches(_name(n.func), failed_scope.name)
                                and n.args
                                and any(
                                    isinstance(x, ast.Name) and x.id in replay_names
                                    for x in ast.walk(n.args[0])
                                )
                            ]
                            originals = [
                                n
                                for n in ast.walk(caller)
                                if isinstance(n, ast.Call)
                                and n.lineno < assignment.lineno
                                and _name(n.func) in functions
                                and reaches(_name(n.func), failed_scope.name)
                            ]
                            temporary = [
                                n
                                for n in ast.walk(caller)
                                if isinstance(n, ast.Call) and _name(n.func) == "TemporaryDirectory"
                            ]
                            if not validations or not originals or not temporary:
                                continue
                            entry_records = []
                            for entrypoint in entrypoints:
                                for artifact in artifacts:
                                    if Path(artifact["abs_path"]).name != Path(entrypoint).name:
                                        continue
                                    path = Path(artifact["abs_path"])
                                    text = self.read(path, "replay_scope_artifacts")
                                    if text is None:
                                        continue
                                    try:
                                        tree = ast.parse(text)
                                    except SyntaxError:
                                        continue
                                    targets = self.static_write_targets(tree, text, path)
                                    entry_records.append(
                                        {
                                            "path": str(path),
                                            "replay_argv_entry": entrypoint,
                                            "static_write_targets": targets,
                                            "required_filename_seen_as_write_target": any(
                                                filename in w["targets"] for w in targets
                                            ),
                                        }
                                    )
                            capture_records = []
                            for artifact in captured:
                                path = Path(artifact["abs_path"])
                                text = self.read(path, "replay_scope_artifacts")
                                if text is not None:
                                    capture_records.append(
                                        {
                                            "path": str(path),
                                            "size_bytes": path.stat().st_size,
                                            "sha256": artifact.get("sha256"),
                                            "excerpt": text[:600],
                                            "excerpt_truncated": len(text) > 600,
                                        }
                                    )
                            if not capture_records:
                                continue
                            findings.append(
                                {
                                    "category": "execution_scope",
                                    "subtype": "captured_deliverable_replay_reconstruction",
                                    "filename": filename,
                                    "status": "review_required",
                                    "automatic_attribution_eligible": False,
                                    "summary": f"Captured {filename} exists, while the observed missing-file rejection is reachable again after isolated entrypoint replay.",
                                    "captured_evidence": capture_records,
                                    "verifier_source_evidence": [
                                        self.source(n)
                                        for n in [
                                            failed,
                                            *copies,
                                            *runs,
                                            *temporary,
                                            *originals,
                                            assignment,
                                            *validations,
                                            *[
                                                n
                                                for fn in functions.values()
                                                for n in ast.walk(fn)
                                                if isinstance(n, ast.Call)
                                                and _name(n.func) == failed_scope.name
                                            ],
                                        ]
                                    ],
                                    "failure_evidence": [
                                        {
                                            "path": str(p),
                                            "excerpt": text[:1800],
                                            "excerpt_truncated": len(text) > 1800,
                                        }
                                        for p, text in self.failures
                                    ],
                                    "required_filename_seen_in_copy_literals": any(
                                        filename in _strings(copy) for copy in copies
                                    ),
                                    "replay_entrypoints": entry_records,
                                    "public_requirement_evidence": self.doc_refs(
                                        [filename, "reproduc", "replay"]
                                    ),
                                    "uncertainty": "Static call flow identifies original and replay validation scopes but cannot prove which invocation raised without stage-specific runtime evidence. Static write targets are incomplete for dynamic paths, imports and subprocesses. Public disclosure and scientific correctness require independent review; original verdict is unchanged.",
                                }
                            )
        return list(
            {
                (f["filename"], tuple(e["path"] for e in f["captured_evidence"])): f
                for f in findings
            }.values()
        )

    @staticmethod
    def static_write_targets(tree: ast.AST, text: str, path: Path) -> list[dict]:
        assignments = {
            target.id: node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        def filenames(node, seen=None):
            seen = set() if seen is None else seen
            values = _strings(node)
            for name in [x.id for x in ast.walk(node) if isinstance(x, ast.Name)]:
                if name in assignments and name not in seen:
                    seen.add(name)
                    values.extend(filenames(assignments[name], seen))
            return [s for s in values if re.search(r"\.[A-Za-z0-9]{1,8}$", s)]

        writes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            target = None
            if name in {"write_text", "write_bytes"} and isinstance(node.func, ast.Attribute):
                target = node.func.value
            elif name == "open" and node.args:
                modes = _strings(node.args[1]) if len(node.args) > 1 else []
                modes.extend(
                    value
                    for kw in node.keywords
                    if kw.arg == "mode"
                    for value in _strings(kw.value)
                )
                if any(any(flag in mode for flag in "wax+") for mode in modes):
                    target = node.args[0]
            if target is not None:
                writes.append(
                    {
                        "path": str(path),
                        "line_start": node.lineno,
                        "targets": sorted(set(filenames(target))),
                        "excerpt": (ast.get_source_segment(text, node) or "")[:1000],
                    }
                )
        return writes


def _schema_for_file(document, document_path: Path, filename: str):
    stem = Path(filename).stem
    for key in (stem + "_schema", stem + "_json_schema", filename):
        node = document.get(key)
        if isinstance(node, dict) and ("properties" in node or "required" in node):
            return node, "$." + key
    if document_path.stem in {stem + ".schema", stem + "-schema", stem + "_schema"} and (
        "properties" in document or "required" in document
    ):
        return document, "$"
    return None


def _json_type(value):
    return {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "array",
        dict: "object",
    }.get(type(value))


def _public_schema_comparison(location: dict, public: list[tuple[Path, str]]) -> list[dict]:
    """Compare a uniquely located submitted field against explicit public JSON declarations."""
    actual = location.get("submitted_field") or location.get("key_set_difference")
    if not actual or actual.get("truncated"):
        return []
    comparisons = []
    for path, text in public:
        if path.suffix != ".json":
            continue
        try:
            document = json.loads(text)
        except (ValueError, RecursionError):
            continue
        if not isinstance(document, dict):
            continue
        selected = _schema_for_file(document, path, Path(actual["source"]).name)
        if not selected:
            continue
        node, pointer = selected
        for key, _index in re.findall(r"\.([\w-]+)|\[(\d+)\]", actual["pointer"]):
            if key:
                properties = node.get("properties")
                node = properties.get(key) if isinstance(properties, dict) else None
                pointer += f".properties.{key}"
            else:
                node = node.get("items")
                pointer += ".items"
            if not isinstance(node, dict):
                break
        if not isinstance(node, dict):
            continue
        record = {"source": str(path), "pointer": pointer, "submitted_pointer": actual["pointer"]}
        if "value" in actual:
            declared_type = node.get("type")
            types = [declared_type] if isinstance(declared_type, str) else declared_type
            if isinstance(types, list) and all(isinstance(kind, str) for kind in types):
                observed = _json_type(actual["value"])
                record.update(
                    declared_types=types,
                    submitted_type=observed,
                    type_matches=observed in types or (observed == "integer" and "number" in types),
                )
            allowed = node.get("enum")
            if (
                isinstance(allowed, list)
                and len(allowed) <= 32
                and all(
                    item is None or isinstance(item, (str, bool, int, float)) for item in allowed
                )
            ):
                record.update(
                    declared_enum=allowed,
                    enum_matches=any(
                        item == actual["value"] and type(item) is type(actual["value"])
                        for item in allowed
                    ),
                )
        else:
            required = node.get("required")
            if isinstance(required, list) and all(isinstance(k, str) for k in required):
                record.update(
                    required_keys=required[:50],
                    missing_required_keys=sorted(set(required) - set(actual["submitted_keys"]))[
                        :50
                    ],
                    verifier_missing_public_required_keys=sorted(
                        set(required) - set(actual["expected_keys"])
                    )[:50],
                )
            properties = node.get("properties")
            closed = (
                node.get("additionalProperties", node.get("additionalProperties_allowed")) is False
            )
            if closed and isinstance(properties, dict):
                record.update(
                    closed_key_set=True,
                    unexpected_public_keys=sorted(
                        set(actual["submitted_keys"]) - properties.keys()
                    )[:50],
                )
        if len(record) > 3:
            comparisons.append(record)
        if len(comparisons) == 4:
            break
    return comparisons


def _public_output_excerpts(location: dict, public: list[tuple[Path, str]]) -> list[dict]:
    """Retain named output declarations without interpreting prose or YAML."""
    actual = location.get("submitted_field") or location.get("key_set_difference")
    if not actual:
        return []
    name = Path(actual["source"]).name
    pattern = re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])")
    field_names = re.findall(r"\.([\w-]+)", actual.get("pointer", ""))
    if field_names:
        field_pattern = re.compile(r"(?<![\w.-])" + re.escape(field_names[-1]) + r"(?![\w.-])")
    else:
        field_pattern = None
    excerpts = []
    for path, text in public:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if pattern.search(line) or (field_pattern and field_pattern.search(line)):
                end = index + 4 if path.suffix in {".yaml", ".yml"} else index + 1
                excerpts.append(
                    {
                        "source": str(path),
                        "line": index + 1,
                        "excerpt": "\n".join(lines[index:end])[:580],
                    }
                )
                break
        if len(excerpts) >= 4:
            break
    return excerpts


def _markdown_required_fields(text, name):
    """Read a named Markdown section's explicit required top-level field table."""
    lines = text.splitlines()
    for start, heading in enumerate(lines):
        if not re.fullmatch(r"#{1,6}\s+`?" + re.escape(name) + r"`?\s*", heading):
            continue
        for index in range(start + 1, min(start + 12, len(lines))):
            if "必需字段如下" not in lines[index] and "required fields" not in lines[index].lower():
                continue
            fields = {}
            for row in lines[index + 1 : index + 24]:
                entry = re.match(r"\s*\|\s*`([\w.-]+)`\s*\|\s*(.*?)\s*\|\s*$", row)
                if entry:
                    fields[entry[1]] = entry[2][:320]
                elif fields:
                    break
            if fields:
                return fields, index + 1
    return {}, None


def _public_yaml_fields(text, name):
    """Locate immediate literal field names under artifacts/<filename> in a block mapping."""
    lines = text.splitlines()
    root = None
    indent = None
    fields = []
    for index, line in enumerate(lines, 1):
        if root is None:
            if re.fullmatch(r"artifacts:\s*(?:#.*)?", line):
                root = 0
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        padding = len(line) - len(line.lstrip(" "))
        if padding == 0:
            break
        if root == 0:
            if line.strip() == name + ":":
                root = index
                indent = padding
            continue
        if padding <= indent:
            break
        match = re.match(r" +([\w.-]+):", line)
        if match:
            fields.append((padding, match[1]))
    if not fields or not root:
        return [], None
    first = min(padding for padding, _ in fields)
    return [key for padding, key in fields if padding == first], root


def _captured_markdown_shape(location, review):
    """Connect the recorded shape check and one explicitly declared JSON report."""
    detail = location["detail"]
    match = re.fullmatch(r"([\w.-]+): (expected a list|field set differs)", detail)
    if not match or not location.get("matching_rule") or not review.evidence.get("trial_dir"):
        return None
    trial = Path(review.evidence["trial_dir"])
    choices = []
    for path, value in review.submitted:
        if path.suffix.lower() != ".md" or not isinstance(value, dict):
            continue
        for doc, text in review.public:
            fields, line = _markdown_required_fields(text, path.name)
            if fields and (match[1] in fields or match[2] == "field set differs"):
                choices.append((path, value, doc, fields, line))
    if len(choices) != 1:
        return None
    path, value, doc, fields, line = choices[0]
    if match[2] == "expected a list":
        if match[1] not in value or isinstance(value[match[1]], list):
            return None
        actual = {
            "source": str(path.relative_to(trial)),
            "pointer": "$." + match[1],
            "observed_type": _json_type(value[match[1]]),
        }
        if isinstance(value[match[1]], dict):
            actual["submitted_keys"] = sorted(value[match[1]])[:50]
        public = {"source": str(doc), "line": line, "declaration": fields[match[1]]}
        return actual, [public]
    if not isinstance(value, dict):
        return None
    # The table states required keys. A key not listed may still be optional.
    return (
        {
            "source": str(path.relative_to(trial)),
            "pointer": "$",
            "observed_type": "object",
            "submitted_keys": sorted(value)[:50],
        },
        [
            {
                "source": str(doc),
                "line": line,
                "required_keys": sorted(fields),
                "missing_required_keys": sorted(fields.keys() - value.keys()),
                "keys_unlisted_in_required_table": sorted(value.keys() - fields.keys())[:50],
            }
        ],
    )


def _sealed_replay_shape(detail, review):
    """Read an available retained candidate result only when its receipt verifies the bytes."""
    pointer = next(iter(_JSON_SHAPE_PATH.findall(detail)), None)
    if not pointer or not review.evidence.get("trial_dir"):
        return None
    trial = Path(review.evidence["trial_dir"])
    verifier = trial / "verifier"
    if verifier.is_symlink() or not verifier.resolve().is_relative_to(trial.resolve()):
        return None
    receipts = [p for p in verifier.glob("candidate-*.result.json.record") if p.is_file()]
    if len(receipts) != 1:
        return None
    receipt = receipts[0]
    try:
        if receipt.is_symlink() or receipt.stat().st_size > 4096:
            return None
        seal = json.loads(receipt.read_text())
        name = seal["snapshot"]
        size = seal["size_bytes"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not re.fullmatch(r"candidate-\d+\.[a-f0-9]+\.result\.json", name)
            or type(size) is not int
            or not 1 <= size <= MAX_FILE_BYTES
        ):
            return None
        result = verifier / name
        if result.is_symlink() or not result.is_file() or result.stat().st_size != size:
            return None
        with result.open("rb") as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != seal["sha256"]:
            return None
        value = json.loads(raw)
        for key, index in re.findall(r"\.([\w-]+)|\[(\d+)\]", pointer):
            value = value[key] if key else value[int(index)]
        output = {
            "source": str(result.relative_to(trial)),
            "pointer": pointer,
            "observed_type": _json_type(value),
            "scope": "verifier_retained_replay",
            "receipt": str(receipt.relative_to(trial)),
            "sha256": seal["sha256"],
        }
        if isinstance(value, dict):
            output["submitted_keys"] = sorted(value)[:50]
        return output
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None


def _declared_comparator_shape(pointer, rule, parsed):
    """Read explicit names at the unique optional-record comparison for this pointer."""
    calls = []
    for source, tree, parents in parsed:
        if source.path != rule["source"]:
            continue
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and _name(call.func) == "compare_optional_record"
                and len(call.args) >= 4
                and isinstance(call.args[3], ast.Constant)
                and call.args[3].value == pointer
                and isinstance(call.args[1], ast.Name)
                and isinstance(call.args[2], ast.Set)
            ):
                continue
            scope = call
            while scope in parents and not isinstance(scope, ast.FunctionDef):
                scope = parents[scope]
            assignments = [
                node
                for node in ast.walk(scope)
                if isinstance(node, ast.Assign)
                and node.lineno < call.lineno
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == call.args[1].id
            ]
            if len(assignments) == 1 and isinstance(assignments[0].value, ast.Dict):
                keys = assignments[0].value.keys
                optional = call.args[2].elts
                if all(
                    isinstance(k, ast.Constant) and isinstance(k.value, str)
                    for k in keys + optional
                ):
                    calls.append(
                        (
                            sorted(k.value for k in keys),
                            sorted(k.value for k in optional),
                            call.lineno,
                        )
                    )
    if len(calls) != 1:
        return {}
    expected, optional, line = calls[0]
    return {"expected_keys": expected, "optional_keys": optional, "comparator_call_line": line}


def _structured_contract_item(location, review, parsed):
    """Emit a reviewable three-way comparison for a recorded structural rejection."""
    rule = location.get("matching_rule")
    if not rule:
        return None
    detail = location["detail"]
    actual = location.get("key_set_difference") or location.get("submitted_field")
    scorer_types = location.get("scorer_expected_types") or []
    public = list(location.get("public_schema_comparison") or [])
    described_type = re.search(
        r"expected type (integer|number|object|array|boolean|string|null)(?:\b|$)", detail
    )
    if described_type:
        scorer_types = [described_type[1]]
    mark = _captured_markdown_shape(location, review)
    if mark:
        actual, markdown_refs = mark
        public.extend(markdown_refs)
    replay_shape = {}
    if not actual and re.search(
        r"output schema or values are invalid: \$.*object keys mismatch", detail
    ):
        actual = _sealed_replay_shape(detail, review)
        if actual:
            replay_shape = _declared_comparator_shape(actual["pointer"], rule, parsed)
            if replay_shape:
                required = set(replay_shape["expected_keys"]) - set(replay_shape["optional_keys"])
                actual["missing_keys"] = sorted(required - set(actual["submitted_keys"]))
                actual["unexpected_keys"] = sorted(
                    set(actual["submitted_keys"]) - set(replay_shape["expected_keys"])
                )
            leaf = actual["pointer"].rsplit(".", 1)[-1]
            public.extend(review.doc_refs([leaf])[:4])
    if not (
        mark
        or location.get("key_set_difference")
        or public
        or (
            scorer_types
            and actual
            and not actual.get("truncated")
            and "value" in actual
            and _json_type(actual["value"]) not in scorer_types
        )
        or re.search(r"output schema or values are invalid: \$.*object keys mismatch", detail)
    ):
        return None
    if actual:
        name = Path(actual["source"]).name
        for path, text in review.public:
            if path.suffix.lower() in {".yaml", ".yml"}:
                fields, line = _public_yaml_fields(text, name)
                if fields:
                    public.append(
                        {"source": str(path), "line": line, "listed_keys": sorted(fields)}
                    )
        for excerpt in (location.get("public_output_excerpts") or [])[:4]:
            if not any(entry["source"] == excerpt["source"] for entry in public):
                public.append(excerpt)
    actual = dict(actual) if actual else {}
    if "value" in actual and not actual.get("truncated"):
        actual["observed_type"] = _json_type(actual["value"])
    if "source" in actual:
        actual["path"] = actual["source"]
    rule_source = {"path": rule["source"], "line_start": rule["line"], **rule}
    explicit = any(
        entry.get("declared_types")
        or entry.get("required_keys")
        or entry.get("listed_keys")
        or entry.get("declared_enum")
        or entry.get("declaration")
        for entry in public
    )
    public_status = (
        "observed_declaration"
        if explicit
        else "excerpt_only"
        if public
        else "not_located_in_bounded_scan"
    )
    return {
        "category": "semantic_rejection",
        "subtype": "structured_contract_difference",
        "field": actual.get("pointer") or next(iter(_JSON_SHAPE_PATH.findall(detail)), "$"),
        "status": "review_required",
        "automatic_attribution_eligible": False,
        "summary": "Compare the recorded scorer condition, captured submission, and available public declarations.",
        "summary_zh": "核对评分条件、已采集提交物与读到的公开约定。",
        "verifier_requirement": rule.get("condition") or detail,
        "actual_submitted_representation": [actual] if actual else [],
        "public_requirement_evidence": public,
        "verifier_source_evidence": [rule_source],
        "source_refs": list(
            filter(
                None,
                (
                    rule.get("source_ref") or rule.get("source"),
                    location["source"],
                    actual.get("source"),
                ),
            )
        ),
        "failure_binding": {
            "status": "source_and_field_bound" if actual else "reported_shape_only",
            "refs": [location["ref"]] if location.get("ref") else [],
            "source": rule_source,
        },
        "public_requirement_coverage": {
            "status": public_status,
            "absence_is_proven": False,
        },
        "structured_comparison": {
            "scorer": {
                "location": rule,
                "reported_rejection": detail,
                **({"expected_types": scorer_types} if scorer_types else {}),
                **({"expected_keys": actual["expected_keys"]} if "expected_keys" in actual else {}),
                **replay_shape,
            },
            "submission": {"status": "captured" if actual else "unavailable", **actual},
            "public": {
                "status": public_status,
                "evidence": public,
                "absence_is_proven": False,
            },
        },
        "uncertainty": "The stored rejection is bound to a scorer condition. Check the effective replay artifact and task revision before assigning responsibility.",
    }


_JSON_SHAPE_PATH = re.compile(r"\$(?:\.[\w-]+|\[\d+\])+")


def audit_contract_review(evidence: dict, *, verifier_sources=None) -> dict:
    """Inspect saved paths in collector evidence; return suggestions without mutation.

    Required keys: task_dir, trial_dir, artifacts (collector artifact records).
    Missing or bounded-out source coverage causes abstention, never a clean bill
    of health. Item categories are semantic_rejection, hidden_format_requirement,
    definition_ambiguity; every item remains review_required.
    """
    review = _Review(evidence)
    review.load()
    task = Path(evidence["task_dir"]) if evidence.get("task_dir") else None
    snapshot = verifier_sources if verifier_sources is not None else collect_verifier_sources(task)
    review.diagnostics.extend(snapshot.diagnostics)
    execution_findings, coverage_findings, parsed = [], [], []
    site_count = 0
    for source in snapshot.sources:
        try:
            review.use_source(task, source)
        except (SyntaxError, ValueError, RecursionError) as exc:
            review.diagnostics.append(
                {
                    "code": "VERIFIER_SOURCE_UNAVAILABLE",
                    "path": source.path,
                    "error_type": type(exc).__name__,
                }
            )
            continue
        parsed.append((source, review.tree, review.parents.copy()))
        site_count += len(review.sites)
        if not review.sites:
            continue
        review.review()
        execution_findings.extend(review.execution_scope_review())
        coverage_findings.extend(review.requirement_coverage_review())
    locations = locate_failures(
        evidence,
        ast.Module(body=[], type_ignores=[]),
        {},
        None,
        review.submitted,
        review.failures,
        parsed_sources=parsed,
    )
    compared = set()
    for location in locations:
        comparison = _public_schema_comparison(location, review.public)
        if comparison:
            location["public_schema_comparison"] = comparison
        excerpts = _public_output_excerpts(location, review.public)
        if excerpts:
            location["public_output_excerpts"] = excerpts
        item = _structured_contract_item(location, review, parsed)
        if item is not None:
            key = (item["field"], item["verifier_source_evidence"][0]["source"], location["detail"])
            if key not in compared:
                compared.add(key)
                item["review_id"] = f"contract_review:{len(review.items) + 1}"
                review.items.append(item)
    return {
        "schema_version": 1,
        "failure_locations": locations,
        "review_items": review.items,
        "execution_scope_findings": execution_findings,
        "requirement_coverage_findings": coverage_findings,
        "coverage": review.coverage,
        "source_inspection": {
            "mode": "static_ast_only",
            "verifier_path": str(task / parsed[0][0].path) if parsed else None,
            "parsed": bool(parsed),
            "source_files": [
                {
                    "path": source.path,
                    "sha256": source.sha256,
                    "archive_path": source.container,
                    "archive_member": source.member,
                }
                for source, _, _ in parsed
            ],
            "failure_sites_matched": site_count,
            "encoded_source_detected": snapshot.encoded_source_detected,
            "encoded_source_decoded": snapshot.encoded_source_decoded,
            "encoded_source_policy": "Verified archives are bounded and read in memory. Current source may differ from the recorded execution version.",
        },
        "diagnostics": review.diagnostics,
        "verdict_policy": "Review suggestions only; original verdict, reward and scientific checks remain unchanged.",
    }
