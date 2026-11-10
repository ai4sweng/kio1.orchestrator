.DEFAULT_GOAL := help

VENV        := .venv
PYTHON      := $(VENV)/bin/python
PIP         := $(PYTHON) -m pip
BOOTSTRAP_PYTHON ?= python3
SOURCES     := *.py ai/ api/ components/ workflow/ tests/
MYPY_SOURCES := $(wildcard *.py ai/*.py api/*.py components/*.py workflow/*.py tests/*.py)
TESTS       := $(wildcard tests/test_*.py)
DOCKER      ?= docker
KIO1_IMAGE  ?= kio1-orchestrator:latest
KIO1_NETWORK ?= kio1-network
AGENT_CONFIG ?= agents.example.json
OLLAMA_URL  ?= http://host.docker.internal:11434

KIO1_DOCKER_ARGS = --network "$(KIO1_NETWORK)" \
	--add-host=host.docker.internal:host-gateway \
	-v "$(CURDIR)/config.json:/app/config.json:ro" \
	-v "$(CURDIR)/chats:/app/chats" \
	-v "$(CURDIR)/logs:/app/logs" \
	-v "$(CURDIR)/assets:/app/assets" \
	-e GROQ_API_KEY -e OPENROUTER_API_KEY \
	$(if $(strip $(AGENT_CONFIG)),-v "$(abspath $(AGENT_CONFIG)):/app/agents.json:ro" -e KIO1_AGENT_CONFIG=/app/agents.json) \
	-e KIO1_OLLAMA_ENDPOINT="$(OLLAMA_URL)"
KIO1_ONLY_NETWORK_ARGS = $(if $(strip $(KIO1_NETWORK)),--network "$(KIO1_NETWORK)")
KIO1_ONLY_DOCKER_ARGS = $(KIO1_ONLY_NETWORK_ARGS) \
	--add-host=host.docker.internal:host-gateway \
	-v "$(CURDIR)/config.json:/app/config.json:ro" \
	-v "$(CURDIR)/chats:/app/chats" \
	-v "$(CURDIR)/logs:/app/logs" \
	-v "$(CURDIR)/assets:/app/assets" \
	-e GROQ_API_KEY -e OPENROUTER_API_KEY \
	-e KIO1_OLLAMA_ENDPOINT="$(OLLAMA_URL)"

# ── Bootstrap ───────────────────────────────────────────────────────────────

.PHONY: install
install: ## Create venv and install all dependencies (runtime + dev)
	$(BOOTSTRAP_PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

# ── Formatting ───────────────────────────────────────────────────────────────

.PHONY: format
format: ## Auto-format code (black + isort)
	$(VENV)/bin/isort --profile black $(SOURCES)
	$(VENV)/bin/black $(SOURCES)

.PHONY: format-check
format-check: ## Verify code formatting (isort + black)
	$(VENV)/bin/isort --profile black --check-only $(SOURCES)
	$(VENV)/bin/black --check $(SOURCES)

# ── Linting & type-checking ──────────────────────────────────────────────────

.PHONY: lint
lint: ## Run ruff linter
	$(VENV)/bin/ruff check $(SOURCES)

.PHONY: typecheck
typecheck: ## Run mypy static type checker
	$(VENV)/bin/mypy $(MYPY_SOURCES)

.PHONY: check
check: lint typecheck ## Run all static checks (lint + typecheck)

# ── Testing ───────────────────────────────────────────────────────────────────

.PHONY: test
test: ## Run KIO1 tests without UI or dummy-project dependencies
	$(VENV)/bin/pytest $(TESTS) -v

.PHONY: test-cov
test-cov: ## Run KIO1 tests with coverage
	$(VENV)/bin/pytest $(TESTS) -v --cov=. --cov-report=term-missing

# ── Docker: independent KIO1 API ──────────────────────────────────────────

.PHONY: prepare docker-network kio1-build
prepare: ## Create host directories used by KIO1 bind mounts
	mkdir -p chats logs assets

docker-network: ## Create the shared Docker network if needed
	$(DOCKER) network inspect "$(KIO1_NETWORK)" >/dev/null 2>&1 || $(DOCKER) network create "$(KIO1_NETWORK)"

kio1-build: ## Build the KIO1 orchestrator image
	$(DOCKER) build -t $(KIO1_IMAGE) .

.PHONY: docker-check ollama-check kio1-run kio1-up kio1-only-run kio1-only-up kio1-start kio1-status kio1-logs kio1-stop
docker-check: ## Check Ollama and KIO7 connectivity from the KIO1 image
	$(DOCKER) run --rm --network "$(KIO1_NETWORK)" \
		--add-host=host.docker.internal:host-gateway \
		-e OLLAMA_URL="$(OLLAMA_URL)" \
		--entrypoint python $(KIO1_IMAGE) \
		-c 'import os, urllib.request; print("Ollama:", urllib.request.urlopen(os.environ["OLLAMA_URL"] + "/api/tags", timeout=5).status); print("KIO7:", urllib.request.urlopen("http://kio7:8000/health", timeout=5).status)'

ollama-check: ## Check Ollama connectivity from the KIO1 image
	$(DOCKER) run --rm $(KIO1_ONLY_NETWORK_ARGS) \
		--add-host=host.docker.internal:host-gateway \
		-e OLLAMA_URL="$(OLLAMA_URL)" \
		--entrypoint python $(KIO1_IMAGE) \
		-c 'import os, urllib.request; print(urllib.request.urlopen(os.environ["OLLAMA_URL"] + "/api/tags", timeout=5).status)'

kio1-run: prepare docker-network ## Run the KIO1 API in the foreground; AGENT_CONFIG enables dispatch
	@test -z "$(AGENT_CONFIG)" || test -f "$(AGENT_CONFIG)"
	$(DOCKER) run --rm --name kio1 $(KIO1_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-up: prepare docker-network ## Start the KIO1 API in the background
	@test -z "$(AGENT_CONFIG)" || test -f "$(AGENT_CONFIG)"
	$(DOCKER) run -d --name kio1 --restart unless-stopped $(KIO1_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-only-run: prepare docker-network ## Run the API without enabling workflow dispatch
	$(DOCKER) run --rm --name kio1 $(KIO1_ONLY_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-only-up: prepare docker-network ## Start the API without enabling workflow dispatch
	$(DOCKER) run -d --name kio1 --restart unless-stopped $(KIO1_ONLY_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-start: ## Resume an existing stopped KIO1 container
	$(DOCKER) start kio1

kio1-status: ## Show KIO1 container status
	$(DOCKER) ps --filter name=kio1

kio1-logs: ## Follow KIO1 logs
	$(DOCKER) logs -f kio1

kio1-stop: ## Stop KIO1 without removing its container
	$(DOCKER) stop kio1

# ── Composite ────────────────────────────────────────────────────────────────

.PHONY: ci
ci: format-check check test ## Non-mutating quality gate

.PHONY: build run up logs status stop
build: kio1-build
run: kio1-run
up: kio1-up
logs: kio1-logs
status: kio1-status
stop: kio1-stop

.PHONY: test-stack
test-stack: ## Verify prebuilt images using synthetic data and isolated containers
	@test -f "$(UI_SOURCE)/Dockerfile" && test -f "$(DUMMY_SOURCE)/Dockerfile" || (printf 'Set UI_SOURCE and DUMMY_SOURCE to explicit checkouts\n'; exit 1)
	$(PYTHON) tests/container_smoke.py --ui-source "$(UI_SOURCE)" --dummy-source "$(DUMMY_SOURCE)" $(STACK_ARGS)

# ── Housekeeping ─────────────────────────────────────────────────────────────

.PHONY: clean
clean: ## Remove generated artefacts
	find . -path ./$(VENV) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -path ./$(VENV) -prune -o -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -path ./$(VENV) -prune -o -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -path ./$(VENV) -prune -o -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -path ./$(VENV) -prune -o -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov .coverage

# ── Help ─────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
