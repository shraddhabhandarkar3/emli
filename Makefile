SHELL := /bin/bash
VENV  := source emli/bin/activate &&

.PHONY: help auth fetch migrate up down logs reset-db test

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

scheduler: ## Run the ingestion scheduler loop (local, Ctrl+C to stop)
	@$(VENV) python -m services.ingestion.scheduler

test: ## Run the test suite
	@$(VENV) python -m pytest tests/ -v

# ── Database ───────────────────────────────────────────────────────────────────
migrate: ## Apply pending Alembic migrations
	@$(VENV) alembic upgrade head

reset-db: ## ⚠ Wipe DB volume and re-run migrations (dev only)
	docker compose -f docker-compose.db.yml down -v
	docker compose -f docker-compose.db.yml up -d
	@echo "Waiting for Postgres to be ready…" && sleep 5
	@$(VENV) alembic upgrade head
	@echo "✓ DB reset complete"

# ── Docker ─────────────────────────────────────────────────────────────────────
up: ## Start Postgres (full compose comes in Ticket 5)
	docker compose -f docker-compose.db.yml up -d

down: ## Stop containers (data preserved in volume)
	docker compose -f docker-compose.db.yml down

logs: ## Follow container logs
	docker compose -f docker-compose.db.yml logs -f
