SHELL := /bin/bash

STACK_NAME ?= fixflow
REGION ?= us-east-1
STAGE ?= dev
ALLOWED_ORIGIN ?= http://localhost:5173
WEB_URL ?= http://localhost:5173
SAM ?= sam

.PHONY: help install test check lint format template-check sam-validate build deploy clean

help:
	@echo "FixFlow development targets"
	@echo ""
	@echo "  make install        Install Python dependencies with uv"
	@echo "  make test           Run backend unit tests"
	@echo "  make check          Compile backend, lint, and validate the SAM template"
	@echo "  make lint           Run ruff over backend and scripts"
	@echo "  make format         Format Python with ruff"
	@echo "  make template-check Validate template YAML and required resources"
	@echo "  make sam-validate   Run 'sam validate --lint' (requires SAM CLI)"
	@echo "  make build          Build the Lambda deployment package (requires SAM CLI)"
	@echo "  make deploy         Deploy the stack (requires SAM CLI and AWS credentials)"
	@echo "  make clean          Remove local build and test artifacts"
	@echo ""
	@echo "Variables: STACK_NAME=$(STACK_NAME) REGION=$(REGION) STAGE=$(STAGE)"
	@echo "           ALLOWED_ORIGIN=$(ALLOWED_ORIGIN) WEB_URL=$(WEB_URL) SAM=$(SAM)"

install:
	uv sync

test:
	uv run pytest

check: template-check lint
	uv run python -m compileall -q backend

lint:
	uv run ruff check backend scripts

format:
	uv run ruff format backend scripts

template-check:
	uv run python scripts/check_template.py

sam-validate:
	@$(SAM) --version >/dev/null 2>&1 || { \
		echo "AWS SAM CLI is not installed."; \
		echo "Install it, then rerun: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"; \
		echo "Or run: make sam-validate SAM='uv tool run --from aws-sam-cli sam'"; \
		exit 1; \
	}
	$(SAM) validate --lint --template infrastructure/template.yaml

build: sam-validate
	$(SAM) build --template infrastructure/template.yaml

deploy: sam-validate
	$(SAM) deploy \
		--template-file infrastructure/template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--resolve-s3 \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides Stage=$(STAGE) AllowedOrigin=$(ALLOWED_ORIGIN) WebUrl=$(WEB_URL)

clean:
	rm -rf .aws-sam .pytest_cache
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
