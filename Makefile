.DEFAULT_GOAL := help

VENV        := .venv
PYTHON      := $(VENV)/bin/python
PIP         := $(PYTHON) -m pip
BOOTSTRAP_PYTHON ?= python3
SOURCES     := *.py ai/ components/ ui/ workflow/ dummy_kios/ tests/
MYPY_SOURCES := $(filter-out tests/test_dummy_kio3.py,$(wildcard *.py ai/*.py components/*.py ui/*.py workflow/*.py dummy_kios/*.py tests/*.py))
TESTS       := $(filter-out tests/test_dummy_kio3.py,$(wildcard tests/test_*.py))
DOCKER      ?= docker
COMPOSE_FILE := dummy_kios/compose.yaml
KIO1_IMAGE  ?= kio1-orchestrator:latest
KIO_DUMMY_NETWORK ?= kio1-network
KIO1_NETWORK ?=
OLLAMA_URL  ?= http://host.docker.internal:11434

export KIO_DUMMY_NETWORK

COMPOSE = $(DOCKER) compose -f $(COMPOSE_FILE)
KIO1_DOCKER_ARGS = --network "$(KIO_DUMMY_NETWORK)" \
	--add-host=host.docker.internal:host-gateway \
	-p 127.0.0.1:5000:5000 \
	-v "$(CURDIR)/config.json:/app/config.json:ro" \
	-v "$(CURDIR)/chats:/app/chats" \
	-v "$(CURDIR)/logs:/app/logs" \
	-v "$(CURDIR)/assets:/app/assets" \
	-v "$(CURDIR)/dummy_kios/agents.dummy.json:/app/agents.dummy.json:ro" \
	-e KIO1_AGENT_CONFIG=/app/agents.dummy.json \
	-e KIO1_OLLAMA_ENDPOINT="$(OLLAMA_URL)"
KIO1_ONLY_NETWORK_ARGS = $(if $(strip $(KIO1_NETWORK)),--network "$(KIO1_NETWORK)")
KIO1_ONLY_DOCKER_ARGS = $(KIO1_ONLY_NETWORK_ARGS) \
	--add-host=host.docker.internal:host-gateway \
	-p 127.0.0.1:5000:5000 \
	-v "$(CURDIR)/config.json:/app/config.json:ro" \
	-v "$(CURDIR)/chats:/app/chats" \
	-v "$(CURDIR)/logs:/app/logs" \
	-v "$(CURDIR)/assets:/app/assets" \
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
test: ## Run tests, excluding the retired KIO3 receiver test
	$(VENV)/bin/pytest $(TESTS) -v

.PHONY: test-cov
test-cov: ## Run tests with coverage, excluding the retired KIO3 receiver test
	$(VENV)/bin/pytest $(TESTS) -v --cov=. --cov-report=term-missing

# ── Docker: KIO1 + dummy agents ────────────────────────────────────────────

.PHONY: prepare docker-network dummy-build kio1-build docker-build
prepare: ## Create host directories used by KIO1 bind mounts
	mkdir -p chats logs assets

docker-network: ## Create the shared Docker network if needed
	$(DOCKER) network inspect "$(KIO_DUMMY_NETWORK)" >/dev/null 2>&1 || $(DOCKER) network create "$(KIO_DUMMY_NETWORK)"

dummy-build: ## Build the KIO2-KIO13 dummy image
	$(COMPOSE) build

kio1-build: ## Build the KIO1 orchestrator image
	$(DOCKER) build -t $(KIO1_IMAGE) .

docker-build: dummy-build kio1-build ## Build both KIO1 and dummy images

.PHONY: dummy-up dummy-up-detached dummy-status dummy-logs dummy-stop dummy-down
dummy-up: docker-network ## Run dummy agents in the foreground
	$(COMPOSE) up --no-log-prefix --no-color

dummy-up-detached: docker-network ## Start dummy agents in the background and wait for health
	$(COMPOSE) up -d --wait

dummy-status: ## Show dummy agent status
	$(COMPOSE) ps

dummy-logs: ## Follow dummy agent task logs
	$(COMPOSE) logs -f --no-log-prefix --no-color

dummy-stop: ## Stop dummy agents without removing their containers
	$(COMPOSE) stop

dummy-down: ## Stop and remove dummy agent containers
	$(COMPOSE) down

.PHONY: docker-check ollama-check kio1-run kio1-up kio1-only-run kio1-only-up kio1-start kio1-status kio1-logs kio1-stop
docker-check: ## Check Ollama and KIO7 connectivity from the KIO1 image
	$(DOCKER) run --rm --network "$(KIO_DUMMY_NETWORK)" \
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

kio1-run: prepare docker-network ## Run KIO1 in the foreground
	$(DOCKER) run --rm --name kio1 $(KIO1_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-up: prepare docker-network ## Start KIO1 in the background with restart policy
	$(DOCKER) run -d --name kio1 --restart unless-stopped $(KIO1_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-only-run: prepare ## Run KIO1 without the dummy-agent registry or network
	$(DOCKER) run --rm --name kio1 $(KIO1_ONLY_DOCKER_ARGS) $(KIO1_IMAGE)

kio1-only-up: prepare ## Start standalone KIO1 in the background with restart policy
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
ci: format check test ## Full CI pipeline: format + lint + typecheck + tests

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
