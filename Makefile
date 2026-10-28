# NightWatch developer tasks. Run `make help` to list them.
#
# Works with GNU make on Linux and macOS. On Windows, run it from Git Bash
# (install with: winget install ezwinports.make).

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV ?= .venv
ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
else
  BIN := $(VENV)/bin
endif
PY := $(BIN)/python

PY_SRC := nightwatch tests
TF_DIR := infra
# Every folder with a main.tf under infra/ is a Terraform root or module to validate.
TF_ROOTS := $(sort $(dir $(wildcard $(TF_DIR)/*/main.tf $(TF_DIR)/*/*/main.tf $(TF_DIR)/*/*/*/main.tf)))

.PHONY: help install fmt lint typecheck test check security tf-fmt tf-check clean

help: ## Show this list
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

$(PY):
	python -m venv $(VENV)

install: $(PY) ## Create .venv, install NightWatch + dev tools, enable git hooks
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"
	@if [ -d .git ] && [ -z "$${CI:-}" ]; then $(BIN)/pre-commit install; fi

fmt: ## Auto-format Python and Terraform
	$(PY) -m ruff check --fix $(PY_SRC)
	$(PY) -m black $(PY_SRC)
	terraform fmt -recursive $(TF_DIR)

lint: ## Lint with ruff and check formatting with black
	$(PY) -m ruff check $(PY_SRC)
	$(PY) -m black --check $(PY_SRC)

typecheck: ## Strict type checks with mypy
	$(PY) -m mypy

test: ## Unit tests with coverage (integration tests are opt-in: pytest -m integration)
	$(PY) -m pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs on the Python code

security: ## bandit (code) and pip-audit (known-vulnerable dependencies)
	$(PY) -m bandit -c pyproject.toml -r nightwatch -q
	$(PY) -m pip_audit --skip-editable --progress-spinner off

tf-fmt: ## Format Terraform files
	terraform fmt -recursive $(TF_DIR)

tf-check: ## terraform fmt check, then validate every module (no AWS access needed)
	terraform fmt -check -recursive $(TF_DIR)
	@for d in $(TF_ROOTS); do \
	  echo "validate $$d"; \
	  terraform -chdir=$$d init -backend=false -input=false >/dev/null; \
	  terraform -chdir=$$d validate; \
	done

clean: ## Remove caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
