SHELL := /bin/bash
VENV  := source emli/bin/activate &&

.PHONY: help auth fetch etl scheduler test migrate reset-db up down logs pull-model

# ── Default ────────────────────────────────────────────────────────────────────
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── One-time setup ─────────────────────────────────────────────────────────────
auth: ## [RUN ONCE] Gmail OAuth — opens browser, saves token to token/
	@$(VENV) python -m services.ingestion.token_manager

# ── Local dev ──────────────────────────────────────────────────────────────────
fetch: ## Run the email ingestion script once (local)
	@$(VENV) python -m services.ingestion.run_fetch

etl: ## Run the applications ETL job (email_events → applications table)
	@$(VENV) python -m services.etl.run_etl

scheduler: ## Run the ingestion scheduler loop (local, Ctrl+C to stop)
	@$(VENV) python -m services.ingestion.scheduler

test: ## Run the test suite
	@$(VENV) python -m pytest tests/ -v

# ── Database ───────────────────────────────────────────────────────────────────
migrate: ## Apply pending Alembic migrations
	@$(VENV) alembic upgrade head

reset-db: ## ⚠ Wipe DB volume and re-run migrations (dev only)
	docker compose stop postgres
	docker volume rm emli_emli_pgdata || true
	docker compose up -d postgres
	@echo "Waiting for Postgres to be ready…" && sleep 5
	@$(VENV) alembic upgrade head
	@echo "✓ DB reset complete"

# ── Docker ─────────────────────────────────────────────────────────────────────
up: ## Start Postgres + Ollama
	docker compose up -d

pull-model: ## [RUN ONCE] Pull the Ollama model into the volume (~2 GB)
	docker exec emli_ollama ollama pull $$(grep OLLAMA_MODEL .env | cut -d= -f2)

down: ## Stop containers (data preserved in volumes)
	docker compose down

logs: ## Follow all container logs
	docker compose logs -f
