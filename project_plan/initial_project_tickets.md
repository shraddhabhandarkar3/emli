# EMLI — Job Application Tracker: Work Tickets

## Project Overview

A fully local, dockerized pipeline that monitors a student's Gmail inbox, classifies job-related emails using a small language model, persists them in a local database, and syncs a Notion page — all running on a cron schedule with zero manual intervention after setup.

---

## Ticket 1 — Gmail Auth & Incremental Email Ingestion

**Goal:** Reliably pull new, unprocessed emails from Gmail on a schedule, handling OAuth lifecycle automatically.

### Scope
- Implement **OAuth 2.0** flow for Gmail API using a `credentials.json` from Google Cloud Console
- Store and auto-refresh tokens; use **offline access** + long-lived refresh tokens so re-auth is never needed in production
- Persist a **`last_processed_timestamp`** (or Gmail `historyId`) in the DB so each cron run only fetches emails since the last run (incremental/idempotent processing)
- Filter emails server-side using Gmail query syntax (e.g. date ranges, label filters) to reduce API calls
- Expose a clean `fetch_new_emails()` function that returns structured email objects `{id, subject, sender, body, date}`
- Cron schedule: configurable via `FETCH_INTERVAL` env var (e.g. every 15 min)

### Key Design Decisions
- Use `google-auth-oauthlib` + `google-api-python-client`
- Token stored in a Docker volume (persisted across restarts)
- First-run: a one-time CLI OAuth flow generates the token; subsequent runs are fully automatic
- Rate-limit handling with exponential backoff

### Deliverables
- `services/ingestion/gmail_client.py`
- `services/ingestion/token_manager.py`
- `services/ingestion/scheduler.py` (APScheduler or cron wrapper)
- Unit tests for token refresh and incremental fetch logic

---

## Ticket 2 — Email Classifier (Local SLM)

**Goal:** For each new email, determine (a) is it job-application-related? and (b) what category does it fall into?

### Categories
| Label | Description |
|---|---|
| `applied` | Confirmation of application submission |
| `interview_scheduled` | Interview invite or scheduling |
| `interview_completed` | Post-interview follow-up |
| `assessment` | Take-home test / coding challenge |
| `offer_extended` | Offer letter received |
| `rejected` | Rejection notice |
| `withdrawn` | Candidate withdrew |
| `ghosted` | No reply after X days (derived, not classified) |
| `other_job_related` | Job-related but doesn't fit above |

### Scope
- Run a **local LLM** (e.g. **Ollama** with `llama3.2:3b` or `phi3:mini`) as a sidecar Docker service
- Two-stage classification prompt chain:
  1. **Stage 1 — Relevance filter:** "Is this email related to a job application?" → yes/no (fast, cheap)
  2. **Stage 2 — Category classifier:** If yes, classify into one of the categories above
- Structured output via JSON-mode or constrained decoding (no free-form hallucination)
- Extract metadata during classification: `company_name`, `role_title`, `application_date` (best-effort via regex + LLM)
- Fallback: if LLM confidence is low, label as `needs_review` for manual triage

### Key Design Decisions
- Ollama runs as a separate Docker service; classifier calls it via HTTP (`http://ollama:11434`)
- Model pulled automatically on first start via `ollama pull` in entrypoint
- Prompt templates stored as versioned `.txt` files for easy iteration
- Classification results include a `raw_llm_response` field for debugging

### Deliverables
- `services/classifier/llm_client.py`
- `services/classifier/prompts/` (stage1.txt, stage2.txt)
- `services/classifier/classifier.py`
- Unit tests with mocked LLM responses
- Integration test against live Ollama instance

---

## Ticket 3 — Database Schema & Storage Layer

**Goal:** Persist all job-related emails and pipeline state in a fast, local, queryable database.

### Database Choice
**PostgreSQL** — best balance of local performance, rich query support, and ecosystem tooling. Runs as a Docker service.

### Schema

```sql
-- Tracks pipeline state (incremental ingestion cursor)
CREATE TABLE pipeline_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- One row per unique job application (deduplicated by deterministic hash of company+role)
CREATE TABLE applications (
  application_id  UUID PRIMARY KEY, -- Deterministic SHA-256 hash
  company_name    TEXT NOT NULL,
  role_title      TEXT,
  applied_date    DATE,
  source          TEXT,          -- e.g. LinkedIn, company site
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- One row per classified email (append-only base table)
CREATE TABLE email_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gmail_id        TEXT UNIQUE NOT NULL,   -- Gmail message ID (idempotency key)
  application_id  UUID,                   -- Grouping key (deterministic hash), NO foreign key constraint
  category        TEXT NOT NULL,
  subject         TEXT,
  sender          TEXT,
  received_at     TIMESTAMPTZ,
  raw_body        TEXT,
  llm_response    JSONB,
  confidence      FLOAT,
  needs_review    BOOLEAN DEFAULT FALSE,
  notion_synced   BOOLEAN DEFAULT FALSE,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Scope
- **Alembic** for schema migrations (version-controlled, reproducible)
- **SQLAlchemy** ORM for all DB access (no raw SQL in application code)
- Deduplication: `gmail_id` as idempotency key prevents double-processing directly at the DB level (PostgreSQL `ON CONFLICT DO NOTHING`)
- Decoupled Architecture: `email_events` acts as an append-only log. The extraction script (Ticket 2) strictly inserts here and does not upsert `applications`.
- `notion_synced` flag used by the sync job to track what's been pushed

### Deliverables
- `db/models.py`
- `db/migrations/` (Alembic env + initial migration)
- `db/repository.py` (CRUD rules enforcing append-only email insertion)

---

## Ticket 4 — Applications ETL Job

**Goal:** Asynchronously rebuild and update the localized `applications` cache downstream from the append-only `email_events` log.

### Scope
- A standalone Python script that queries `email_events` incrementally (e.g. using time windows or fetching all events)
- Groups events by `application_id`
- Upserts records into the `applications` table natively parsing the events to deduce values like `applied_date` or current overall status
- Follows a strict one-way data flow: `email_events` -> ETL script -> `applications`

### Deliverables
- `services/etl/applications_builder.py`
- `services/etl/scheduler.py`
- Unit tests for upsert prioritization rules

---

## Ticket 5 — Notion Sync Job

**Goal:** Maintain a live Notion page (or database) that reflects the current state of all job applications, updated on a cron schedule.

### Notion Structure
A **Notion Database** (not a plain page) with one row per application:

| Property | Type | Source |
|---|---|---|
| Company | Title | `applications.company_name` |
| Role | Text | `applications.role_title` |
| Status | Select | Latest `email_events.category` |
| Applied Date | Date | `applications.applied_date` |
| Last Activity | Date | Latest `email_events.received_at` |
| Email Count | Number | Count of related events |
| Needs Review | Checkbox | Any event with `needs_review=true` |

### Scope
- Use **Notion API** (`notion-client` Python SDK) with an Integration Token
- Cron runs every hour (configurable); queries DB for rows where `notion_synced = FALSE` or `updated_at > last_sync`
- **Upsert logic**: match on `company_name + role_title`; create if not found, update if status changed
- After successful sync, mark `email_events.notion_synced = TRUE`
- On failure: log error, retry next cycle (no data loss — DB is source of truth)

### Deliverables
- `services/notion_sync/notion_client.py`
- `services/notion_sync/sync_job.py`
- `services/notion_sync/scheduler.py`
- Unit tests with mocked Notion API responses

---

## Ticket 6 — Dockerization & Plug-and-Play Setup

**Goal:** The entire stack runs with `docker compose up` after a user adds their keys to a `.env` file. No local installs required beyond Docker.

### Services in `docker-compose.yml`

| Service | Image | Role |
|---|---|---|
| `postgres` | `postgres:16-alpine` | Database |
| `ollama` | `ollama/ollama` | Local LLM inference |
| `ingestion` | Custom (Python 3.12-slim) | Gmail fetch + classify + store |
| `applications_etl` | Custom (Python 3.12-slim) | DB → Applications ETL Upsert |
| `notion_sync` | Custom (Python 3.12-slim) | DB → Notion sync |

### Scope

**`docker-compose.yml`**
- Named volumes for Postgres data, Ollama models, and Gmail token
- Health checks on all services; `ingestion` and `notion_sync` depend on `postgres` + `ollama` being healthy
- Shared `.env` file for all secrets (never committed)

**`.env.example`** (committed to repo)
```env
# Gmail
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Notion
NOTION_TOKEN=
NOTION_DATABASE_ID=

# Ollama
OLLAMA_MODEL=llama3.2:3b

# Schedule (cron expressions)
FETCH_INTERVAL_MINUTES=15
SYNC_INTERVAL_MINUTES=60
```

**First-run experience**
1. `cp .env.example .env` and fill in keys
2. `docker compose up -d`
3. On first start, a one-time OAuth setup container prints a URL → user pastes into browser → token saved to volume
4. All jobs start automatically; Notion page populates within the first cycle

**`Makefile` targets**
```
make setup       # cp .env.example .env (with instructions)
make up          # docker compose up -d
make logs        # docker compose logs -f
make auth        # re-run OAuth flow if token expires
make reset-db    # drop & recreate DB (dev only)
make test        # run pytest inside containers
```

**Security**
- `.env` and `token/` directory in `.gitignore`
- Secrets passed only via environment variables, never baked into images
- Postgres not exposed on host ports by default

### Deliverables
- `docker-compose.yml`
- `Dockerfile` (shared base, multi-stage for each service)
- `.env.example`
- `Makefile`
- `README.md` with setup instructions (30-second quickstart + detailed docs)
- GitHub Actions CI: lint + unit tests on every PR

---

## Ticket 7 — User-Configurable Model Selection *(final polish)*

**Goal:** Let users swap the Ollama model at any time without touching Docker files or source code.

### Scope
- Add a `config.yaml` at the repo root as the single place to configure the model:
  ```yaml
  classifier:
    model: llama3.2:3b   # change to phi3:mini, gemma2:2b, tinyllama, etc.
    temperature: 0.0
    timeout_seconds: 30
  ```
- On startup, `ingestion` service reads `config.yaml`, calls `ollama pull <model>` if the model isn't already downloaded, then uses it for all classification
- `OLLAMA_MODEL` env var in `.env` overrides `config.yaml` (env takes precedence)
- Add a `make set-model MODEL=phi3:mini` Makefile target that updates `config.yaml` and restarts the ingestion container
- Document a "Supported Models" table in `README.md` with recommended options and their sizes

### Deliverables
- `config.yaml` + `config.example.yaml`
- `services/classifier/config_loader.py`
- Updated `Makefile` with `set-model` target
- Updated `README.md`

---

## Suggested Build Order

```
T3 (DB Schema) → T1 (Ingestion) → T2 (Classifier) → T4 (ETL Job) → T5 (Notion Sync) → T6 (Docker) → T7 (Model Config)
```

Start with the database schema since every other service depends on it. Docker comes last so each service can be developed and tested locally first, then wired together. T7 is a final polish pass once everything is working end-to-end.
