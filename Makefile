PYTHON ?= python3
RUFF ?= $(shell command -v ruff 2>/dev/null || (command -v uv >/dev/null 2>&1 && echo "uv tool run ruff") || echo "$(PYTHON) -m ruff")
FIXTURE_JOB := tests/fixtures/regressions/mixed-job-success-trial/job
# Local smoke output: hidden, gitignored, never part of the shipped skill tree.
DEV_OUT := .dev/smoke
SHARED_DOCS := attribution-protocol.md evidence-schema.md report-schema.md skill-prescription-policy.md taxonomy.md
CODEX_HOME ?= $(HOME)/.codex
DEST ?= $(CODEX_HOME)/skills/case-failure-analyzer

.PHONY: help test test-pytest test-one compile smoke dev dev-min clean lint lint-fix format format-check check-docs check export-skill

help: ## List available targets
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

test: ## Canonical zero-dependency test run (unittest, no install needed)
	$(PYTHON) -m unittest discover -s tests -t . -v

test-pytest: ## Optional pytest run (requires `make dev`)
	$(PYTHON) -m pytest -q

test-one: ## Run one test module: make test-one T=tests.test_attribution_decisions
	$(PYTHON) -m unittest -v $(T)

compile: ## Zero-dependency syntax check over scripts/ and tests/
	$(PYTHON) -m compileall -q scripts tests

lint: ## Check codebase with ruff (syntax, dead code, and anti-patterns)
	$(RUFF) check .

lint-fix: ## Automatically fix safe lint issues (unused imports, etc.)
	$(RUFF) check --fix .

format: ## Format codebase with ruff
	$(RUFF) format .

format-check: ## Check codebase formatting with ruff without writing changes
	$(RUFF) format --check .

check-docs: ## Verify docs/en mirrors match references/ and guide docs (en|zh) integrity
	@drift=""; \
	tmp_ref=$$(mktemp); tmp_en=$$(mktemp); \
	trap 'rm -f "$$tmp_ref" "$$tmp_en"' EXIT; \
	for f in $(SHARED_DOCS); do \
		ref="references/$$f"; \
		en="docs/en/$$f"; \
		if [ -f "$$en" ] && cmp -s "$$ref" "$$en"; then continue; fi; \
		if [ -f "$$en" ] \
			&& [ "$$(head -n 2 "$$ref")" = "$$(head -n 2 "$$en")" ] \
			&& sed -n '3p' "$$en" | grep -q '^\*\*\[English\]' \
			&& [ -z "$$(sed -n '4p' "$$en")" ]; then \
			tail -n +3 "$$ref" > "$$tmp_ref"; \
			tail -n +5 "$$en" > "$$tmp_en"; \
			cmp -s "$$tmp_ref" "$$tmp_en" || drift="$$drift docs/en/$$f"; \
		else \
			drift="$$drift docs/en/$$f"; \
		fi; \
	done; \
	if [ -n "$$drift" ]; then \
		echo "ERROR: docs/en mirrors drifted from references/:"; \
		for f in $$drift; do echo "  - $$f"; done; \
		echo "Fix: copy the body of references/<file>.md into docs/en/<file>.md (keep the language-switch header)."; \
		exit 1; \
	fi; \
	guide_fail=""; \
	for g in "docs/en/guide.md:**[English](guide.md):[简体中文](../zh/guide.md)" "docs/zh/guide.md:**[English](../en/guide.md):[简体中文](guide.md)"; do \
		path="$${g%%:*}"; rest="$${g#*:}"; self="$${rest%%:*}"; cross="$${rest#*:}"; \
		if [ ! -f "$$path" ]; then \
			guide_fail="$$guide_fail missing:$$path"; \
		elif ! sed -n '3p' "$$path" | grep -qF "$$self" || ! sed -n '3p' "$$path" | grep -qF "$$cross"; then \
			guide_fail="$$guide_fail broken-language-switch-header:$$path"; \
		fi; \
	done; \
	if [ -n "$$guide_fail" ]; then \
		echo "ERROR: guide docs (docs/{en,zh}/guide.md) integrity failed:"; \
		for g in $$guide_fail; do echo "  - $$g"; done; \
		echo "Fix: restore the missing guide.md or its line-3 language-switch header (self + cross links)."; \
		exit 1; \
	fi; \
	echo "check-docs: docs/en mirrors match references/ ($(words $(SHARED_DOCS)) shared docs); guide docs (en|zh) intact."

check: lint test check-docs format-check ## Run all checks: lint, tests, docs sync, and formatting
	@echo "check: all checks passed (lint, test, check-docs, format-check)."

smoke: ## End-to-end CLI run against the committed 2-trial fixture
	$(PYTHON) scripts/analyze_case.py --job $(FIXTURE_JOB) --output $(DEV_OUT)
	@echo "--- job_summary.json ---"
	@cat $(DEV_OUT)/job_summary.json

export-skill: ## Export clean production skill (SKILL.md, agents, scripts, references) to DEST
	@if [ -z "$(DEST)" ]; then echo "ERROR: DEST must be specified." >&2; exit 1; fi
	$(PYTHON) scripts/export_skill.py --dest "$(DEST)"

dev: ## Install dev extras (pytest, ruff) into the active interpreter
	$(PYTHON) -m pip install -e ".[dev]"

dev-min: ## Fallback if the editable install misbehaves: install pytest and ruff only
	$(PYTHON) -m pip install pytest ruff

clean: ## Remove bytecode caches, .DS_Store files and local smoke output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache $(DEV_OUT)
	rmdir .dev 2>/dev/null || true
	rm -rf case_failure_analyzer.egg-info
	find . -name .DS_Store -not -path "./.venv/*" -not -path "./.git/*" -delete
