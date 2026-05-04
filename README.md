# emli — Email Job Application Tracker

Automatically pulls job-related emails from Gmail, classifies them with an LLM, and keeps a Notion database up-to-date — no manual data entry required.

```
Gmail → Fetch → Classify (LLM) → ETL → Notion
```

---

## Quickstart (Docker)

> **Requirements:** Docker Desktop · A Gmail account · A Notion workspace

### 1. Clone & configure

```bash
git clone https://github.com/your-username/emli.git
cd emli
make setup          # creates .env from .env.example
```

Open `.env` and fill in the three required values:

```bash
NOTION_TOKEN=secret_...        # https://www.notion.so/my-integrations
NOTION_DATABASE_ID=...         # from your Notion DB URL (see below)
API_KEY=gsk_...                # Groq free key: https://console.groq.com
```

### 2. Google credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services** → **Credentials**
2. Create an **OAuth 2.0 Client ID** (Desktop app type), enable the **Gmail API**
3. Download the JSON and rename it to **`client_secret.json`** in the project root
4. Run the one-time OAuth flow (opens your browser):
   ```bash
   make auth
   ```

### 3. Notion database

1. Create a blank **full-page database** in Notion (any name)
2. Click **Share** → invite your integration
3. Copy the database ID from the URL:
   ```
   https://notion.so/your-workspace/THIS-IS-THE-ID?v=...
   ```
4. Paste it as `NOTION_DATABASE_ID` in `.env`

> The pipeline auto-configures all columns (Company, Role, Status, Applied Date, etc.) on first run — no manual setup needed.

### 4. Run

```bash
make pipeline-docker
```

That's it. Check your Notion database — it should populate within a minute or two.

---

## Daily use

Run the pipeline each morning to pull in new emails:

```bash
make pipeline-docker
```

The pipeline is **incremental** — it only fetches emails since the last run. If it crashes mid-way, re-running picks up exactly where it left off (already-processed emails are skipped).

---

## LLM Options

Set `LLM_PROVIDER` in `.env` to choose your classifier:

| Provider | `LLM_PROVIDER` | `API_BASE_URL` | Cost | Speed |
|---|---|---|---|---|
| **Groq** *(recommended)* | `api` | `https://api.groq.com/openai/v1` | Free (14k req/day) | Fast |
| **NVIDIA NIM** | `api` | `https://integrate.api.nvidia.com/v1` | Free (40 RPM) | Slow |
| **OpenAI** | `api` | *(leave blank)* | Paid | Fast |
| **Ollama** *(local)* | `ollama` | `http://ollama:11434` | Free | Slow |

For Groq (recommended):
```bash
LLM_PROVIDER=api
API_BASE_URL=https://api.groq.com/openai/v1
API_KEY=gsk_...
API_MODEL=llama-3.3-70b-versatile
LLM_TIMEOUT=30
LLM_BATCH_SIZE=0      # Groq handles rate limits gracefully — no manual pausing needed
```

For Ollama (no API key):
```bash
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2:3b
# Pull the model once:
make pull-model
```

---

## Make targets

```
make setup            First-time setup — copy .env.example and print instructions
make auth             Gmail OAuth (one-time, opens browser)
make pipeline-docker  Run full pipeline in Docker ← daily use
make pipeline         Run full pipeline locally (requires Python venv)
make build            Build the pipeline Docker image
make up               Start Postgres + Ollama
make down             Stop all containers
make logs             Follow container logs
make pull-model       Pull the Ollama model into Docker volume (~2 GB)
make fetch            Fetch & classify emails only (local)
make etl              Rebuild applications table (local)
make sync             Sync to Notion (local)
make resync           Force re-sync all rows to Notion
make migrate          Apply pending DB migrations
make reset-db         ⚠ Wipe and recreate DB (dev only)
make test             Run test suite (local)
make test-docker      Run test suite in Docker
```

---

## Troubleshooting

**`invalid_grant: Bad Request`** — OAuth token expired. Run `make auth` to refresh.

**`historyId` cursor issues** — If you want to re-fetch a time window:
```bash
rm token/gmail_state.json
GMAIL_FETCH_DAYS=7   # add to .env temporarily
make pipeline-docker
```
Already-stored emails are skipped automatically — no duplicates.

**Rate limit 429** — The pipeline handles this automatically (sleeps and retries). If using NVIDIA NIM, set `LLM_TIMEOUT=120` in `.env`.

**Notion schema missing columns** — The pipeline auto-creates them on every sync via `ensure_schema`. Just re-run `make sync`.

---

## Architecture

```
services/
  ingestion/      Gmail fetch + LLM classify + store → email_events table
  etl/            email_events → applications table (grouping + dedup)
  notion_sync/    applications table → Notion database (upsert)
  classifier/     LLM client (Ollama / OpenAI-compatible API)
db/
  models.py       SQLAlchemy models
  repository.py   CRUD layer
  migrations/     Alembic migrations
```

Data flow:
1. **Fetch** — Gmail History API pulls emails since last run (incremental)
2. **Classify** — LLM determines if job-related, extracts company + role + status
3. **Store** — Saves to `email_events` (idempotent via `gmail_id` unique key)
4. **ETL** — Groups events by company+role into `applications`, derives status timeline
5. **Sync** — Upserts each application to Notion, auto-configures DB schema

---

## Development

```bash
# Create venv and install deps
python -m venv emli
source emli/bin/activate
pip install -r requirements.txt

# Start infrastructure
make up

# Run the pipeline locally (faster iteration than Docker)
make pipeline
```
