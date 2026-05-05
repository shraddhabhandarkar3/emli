# emli

Pulls job-related emails from Gmail, classifies them with an LLM, and keeps a Notion database in sync. Runs as a single command — no manual data entry.

```
Gmail → fetch → LLM classify → ETL → Notion
```

---

## Setup

**Requirements:** Docker · Gmail account · Notion workspace

### 1. Clone and configure

```bash
git clone https://github.com/shraddhabhandarkar3/emli.git
cd emli
make setup
```

Fill in `.env` — at minimum:

```bash
NOTION_TOKEN=          # notion.so/my-integrations → create integration
NOTION_DATABASE_ID=    # from the Notion database URL
API_KEY=               # Groq free key: console.groq.com (or see LLM Options below)
```

### 2. Gmail credentials

In [Google Cloud Console](https://console.cloud.google.com): enable the Gmail API, create an OAuth 2.0 Desktop client, download the JSON, and save it as `client_secret.json` in the project root.

```bash
make auth   # opens browser for OAuth, saves token to token/
```

### 3. Notion database

Create a blank full-page database in Notion, share it with your integration, and copy the database ID from the URL. The pipeline auto-creates all columns on first run.

### 4. Build and run

```bash
make build
make pipeline-docker
```

---

## Daily use

```bash
make pipeline-docker
```

Picks up only emails since the last run. If it crashes mid-batch, re-running resumes from the same point — already-processed emails are skipped.

---

## Scheduled mode

**Option A — interval loop (Docker-managed)**

```bash
# Set interval in .env (e.g. 1440 for once a day)
FETCH_INTERVAL_MINUTES=1440

make schedule    # starts in the background, restarts on reboot
make unschedule  # stop it
make logs        # follow output
```

**Option B — specific time (system cron)**

More precise if you want it to run at a fixed time, e.g. every morning at 8 AM:

```bash
crontab -e
```
```
0 8 * * * cd /path/to/emli && make pipeline-docker >> /tmp/emli.log 2>&1
```

Docker only needs to be running when the cron fires at 8:00 AM — you can close it after the pipeline finishes (usually a few minutes). On macOS, enable **"Start Docker Desktop when you log in"** in Docker Desktop → Settings so it's always ready. On Linux, Docker runs as a system service and works without any extra setup.

---

## LLM options

Set `LLM_PROVIDER=ollama` for local inference or `LLM_PROVIDER=api` for an external provider.

| Provider | `API_BASE_URL` | Free tier | Speed |
|---|---|---|---|
| **Groq** *(default)* | `https://api.groq.com/openai/v1` | 14k req/day | Fast |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | 40 RPM | Slow |
| OpenAI | *(leave blank)* | No | Fast |
| Ollama | `http://ollama:11434` | Unlimited | Slow |

For Groq:
```bash
LLM_PROVIDER=api
API_BASE_URL=https://api.groq.com/openai/v1
API_KEY=gsk_...
API_MODEL=llama-3.3-70b-versatile
LLM_TIMEOUT=30
LLM_BATCH_SIZE=0  # Groq handles its own backpressure
```

For Ollama (no API key):
```bash
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2:3b
make pull-model  # one-time, ~2 GB
```

---

## Commands

| Command | Description |
|---|---|
| `make setup` | Copy `.env.example` → `.env`, prompt for run mode |
| `make auth` | Gmail OAuth (one-time) |
| `make build` | Build the pipeline Docker image |
| `make pipeline-docker` | Run full pipeline once in Docker |
| `make schedule` | Start pipeline on a recurring schedule (background) |
| `make unschedule` | Stop the scheduled pipeline |
| `make pipeline` | Run full pipeline locally (requires venv) |
| `make up` / `make down` | Start / stop infrastructure |
| `make logs` | Follow container logs |
| `make pull-model` | Pull Ollama model into Docker volume |
| `make fetch` | Fetch and classify only (local) |
| `make etl` | Rebuild applications table (local) |
| `make sync` | Push to Notion (local) |
| `make resync` | Re-push everything to Notion |
| `make migrate` | Apply Alembic migrations |
| `make reset-db` | ⚠ Wipe and recreate the database |
| `make test` | Run test suite |

---

## Troubleshooting

**`invalid_grant: Bad Request`** — OAuth token expired. Re-run `make auth`.

**Re-fetching a time window:**
```bash
rm token/gmail_state.json
# set GMAIL_FETCH_DAYS=7 in .env temporarily
make pipeline-docker
```
Already-stored emails are skipped automatically.

**429 rate limit** — handled automatically with backoff. For NVIDIA NIM, set `LLM_TIMEOUT=120`.

---

## Architecture

```
services/
  ingestion/    Gmail fetch + LLM classify → email_events
  etl/          email_events → applications (grouping, dedup)
  notion_sync/  applications → Notion (upsert, schema management)
  classifier/   LLM client (Ollama / OpenAI-compatible)
db/
  models.py     SQLAlchemy models
  repository.py CRUD layer
  migrations/   Alembic migrations
```

**Data flow:**
1. Gmail History API fetches emails incrementally (historyId cursor)
2. LLM classifies each email — job-related or not, extracts company/role/status
3. Job-related emails written to `email_events` (idempotent on `gmail_id`)
4. ETL groups events by company+role into `applications`, resolves status
5. Notion sync upserts each application, patching schema on first run

---

## Local development

```bash
python -m venv emli && source emli/bin/activate
pip install -r requirements.txt
make up        # start Postgres + Ollama
make pipeline  # run locally against Docker infra
```
