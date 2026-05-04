SHELL := /bin/bash
VENV  := source emli/bin/activate &&

.PHONY: help setup auth fetch etl sync pipeline pipeline-docker scheduler \
        build test migrate reset-db up down logs pull-model resync

# ── Default ────────────────────────────────────────────────────────────────────
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── First-time setup ───────────────────────────────────────────────────────────
setup: ## Copy .env.example → .env and print setup instructions
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✓ .env created — fill in your keys before running the pipeline."; \
		echo ""; \
		echo "  Required:"; \
		echo "    NOTION_TOKEN        — from https://www.notion.so/my-integrations"; \
		echo "    NOTION_DATABASE_ID  — from your Notion database URL"; \
		echo "    API_KEY             — Groq (free): https://console.groq.com"; \
		echo ""; \
		echo "  Then run: make auth && make pipeline-docker"; \
	else \
		echo "✓ .env already exists."; \
	fi

auth: ## [RUN ONCE] Gmail OAuth — opens browser, saves token to token/
	@$(VENV) python -m services.ingestion.token_manager

# ── Local dev (requires Python venv) ──────────────────────────────────────────
fetch: ## Fetch & classify new emails (local)
	@$(VENV) python -m services.ingestion.run_fetch

etl: ## Rebuild applications table from email_events (local)
	@$(VENV) python -m services.etl.run_etl

sync: ## Sync unsynced applications to Notion (local)
	@$(VENV) python -m services.notion_sync.run_sync

resync: ## Force re-sync ALL applications to Notion (resets synced flags first)
	@$(VENV) python -c "\
from db.session import get_session; from db.models import EmailEvent; \
from sqlalchemy import update; s=get_session().__enter__(); \
s.execute(update(EmailEvent).values(notion_synced=False)); \
print('✓ Reset notion_synced for all rows')"
	@$(VENV) python -m services.notion_sync.run_sync

pipeline: ## Run full pipeline locally: start infra → fetch → ETL → Notion sync
	@echo "── Starting services ──────────────────────────────────"
	@$(MAKE) up
	@echo "── Step 1/3: Fetching emails ──────────────────────────"
	@$(VENV) python -m services.ingestion.run_fetch
	@echo "── Step 2/3: Running ETL ──────────────────────────────"
	@$(VENV) python -m services.etl.run_etl
	@echo "── Step 3/3: Syncing to Notion ────────────────────────"
	@$(VENV) python -m services.notion_sync.run_sync
	@echo "✓ Pipeline complete!"

scheduler: ## Run the full pipeline on a loop (Ctrl+C to stop)
	@$(VENV) python -m services.ingestion.scheduler

# ── Docker pipeline (no local Python required) ─────────────────────────────────
build: ## Build the pipeline Docker image
	docker compose build pipeline

pipeline-docker: ## Run full pipeline in Docker (no local Python needed)
	@echo "── Starting infrastructure ────────────────────────────"
	@docker compose up -d postgres ollama
	@echo "── Running pipeline container ─────────────────────────"
	@docker compose --profile pipeline run --rm pipeline
	@echo "✓ Pipeline complete!"

# ── Testing ────────────────────────────────────────────────────────────────────
test: ## Run the test suite (local)
	@$(VENV) python -m pytest tests/ -v

test-docker: ## Run the test suite inside Docker
	@docker compose --profile pipeline run --rm --entrypoint "" pipeline python -m pytest tests/ -v

# ── Database ───────────────────────────────────────────────────────────────────
migrate: ## Apply pending Alembic migrations
	@$(VENV) alembic upgrade head

reset-db: ## ⚠ Wipe DB volume and re-run migrations (dev only)
	docker compose stop postgres
	docker volume rm emli_pgdata || true
	docker compose up -d postgres
	@echo "Waiting for Postgres to be ready…" && sleep 5
	@$(VENV) alembic upgrade head
	@echo "✓ DB reset complete"

# ── Docker infra ────────────────────────────────────────────────────────────────
up: ## Start Postgres + Ollama
	docker compose up -d

down: ## Stop all containers (data preserved in volumes)
	docker compose down

logs: ## Follow all container logs
	docker compose logs -f

pull-model: ## [RUN ONCE] Pull the Ollama model into the volume (~2 GB)
	docker exec emli_ollama ollama pull $$(grep ^OLLAMA_MODEL .env | cut -d= -f2 | tr -d ' ')
