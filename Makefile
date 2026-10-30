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

PY_SRC := nightwatch shoplite scripts chaos eval tests
TF_DIR := infra
# Root configurations. Validating a root also validates every module it uses.
TF_ROOTS := $(TF_DIR)/bootstrap $(TF_DIR)/envs/dev
TF_ENV := $(TF_DIR)/envs/dev

# Local Terraform and AWS runs use the restricted deployer profile (ADR 0006).
export AWS_PROFILE ?= nightwatch
DISCORD_GUILD_ID ?= 1565026603212210176
GIT_SHA := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo dev)

.PHONY: help install fmt lint typecheck test check security tf-fmt tf-check clean build
.PHONY: bootstrap-plan bootstrap-apply init plan apply destroy chaos-bad-deploy chaos-error-flag
.PHONY: chaos chaos-list chaos-fix chaos-fix-flag discord-endpoint eval
.PHONY: memory-check discord-commands backfill-memory
.PHONY: dashboard dashboard-check dashboard-dev dashboard-deploy
.PHONY: coach forecast

help: ## Show this list
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

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
	$(PY) -m bandit -c pyproject.toml -r nightwatch shoplite scripts chaos eval -q
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

build: ## Build the Lambda zips into .build/ (ShopLite, NightWatch, and the arm64 dependency layer)
	$(PY) -m scripts.build_lambda shoplite --out .build/shoplite-api.zip --version $(GIT_SHA)
	$(PY) -m scripts.build_lambda nightwatch --out .build/nightwatch.zip --extra runbooks:nightwatch/runbooks
	$(PY) -m scripts.build_layer --out .build/nightwatch-deps.zip

# --- AWS. Plans are free to run; every apply uses a saved plan you have reviewed. ---

bootstrap-plan: ## Plan the one-time Terraform state bucket
	terraform -chdir=$(TF_DIR)/bootstrap init -input=false
	terraform -chdir=$(TF_DIR)/bootstrap plan -input=false -out=tfplan

bootstrap-apply: ## Apply the reviewed bootstrap plan
	terraform -chdir=$(TF_DIR)/bootstrap apply -input=false tfplan

init: ## terraform init for dev (needs infra/envs/dev/backend.hcl)
	terraform -chdir=$(TF_ENV) init -input=false -backend-config=backend.hcl

plan: build ## Plan the dev environment and save it for review
	terraform -chdir=$(TF_ENV) plan -input=false -out=tfplan

apply: ## Apply the reviewed dev plan
	terraform -chdir=$(TF_ENV) apply -input=false tfplan

destroy: ## Delete everything in dev (asks before deleting)
	terraform -chdir=$(TF_ENV) destroy -input=false

# --- Chaos lab (uses the nightwatch profile; always restores ShopLite afterwards) ---

chaos-bad-deploy: ## Deploy a buggy ShopLite commit for 4 minutes, check the diagnosis names it
	$(PY) -m chaos.bad_deploy

chaos-error-flag: ## Turn on ShopLite's error flag for 4 minutes and check NightWatch's response
	$(PY) -m chaos.error_flag

chaos: ## Run one chaos scenario: make chaos NAME=lambda-timeout (see make chaos-list)
	$(PY) -m chaos $(NAME)

chaos-list: ## List every chaos scenario and its ground truth
	$(PY) -m chaos --list

eval: ## Run every chaos scenario and score NightWatch (about 2.5 hours), writes eval/results.md
	$(PY) -m eval.run

chaos-fix: ## Deploy a buggy commit and keep it live until you approve NightWatch's rollback
	$(PY) -m chaos.bad_deploy --let-nightwatch-fix

chaos-fix-flag: ## Turn on the error flag and keep it on until you approve NightWatch's fix
	$(PY) -m chaos.error_flag --let-nightwatch-fix

coach: ## Write the alert-quality report now (it also runs every Monday)
	$(PY) -c "import boto3,json; r=boto3.Session(profile_name='$(AWS_PROFILE)',region_name='us-east-2').client('lambda').invoke(FunctionName='nightwatch-coach',Payload=b'{}'); print(r['Payload'].read().decode())"

forecast: ## Make today's forecasts now (they also run every morning)
	$(PY) -c "import boto3; r=boto3.Session(profile_name='$(AWS_PROFILE)',region_name='us-east-2').client('lambda').invoke(FunctionName='nightwatch-forecast',Payload=b'{}'); print(r['Payload'].read().decode())"

dashboard: ## Build the web dashboard into dashboard/dist
	cd dashboard && npm ci --no-audit --no-fund && npm run build

dashboard-check: ## Type-check and test the dashboard (CI runs this too)
	cd dashboard && npm run typecheck && npm test

dashboard-dev: ## Run the dashboard locally against the deployed API (needs NIGHTWATCH_URL)
	cd dashboard && npm run dev

dashboard-deploy: ## Upload the built dashboard and refresh CloudFront (CI does this on merge)
	$(PY) -m scripts.deploy_dashboard --profile $(AWS_PROFILE) \
		--bucket "$$(terraform -chdir=infra/envs/dev output -raw dashboard_bucket)" \
		--distribution "$$(terraform -chdir=infra/envs/dev output -raw dashboard_distribution_id)"

discord-commands: ## Register the /nw slash command in your Discord server (after apply)
	$(PY) -m scripts.discord_commands --guild $(DISCORD_GUILD_ID)

backfill-memory: ## Write postmortems and memories for incidents resolved before Phase 6
	$(PY) -m scripts.backfill_memory

memory-check: ## Run the bad deploy twice: the second diagnosis must cite the first (~30 min)
	$(PY) -m chaos.memory_check bad-deploy

discord-endpoint: ## Point the Discord application's button clicks at NightWatch (after apply)
	$(PY) -m scripts.discord_endpoint --url "$$(terraform -chdir=infra/envs/dev output -raw discord_interactions_url)"

clean: ## Remove caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist .build *.egg-info
