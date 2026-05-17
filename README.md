# emli

Pulls job-related emails from Gmail, classifies them with an LLM, and keeps a Notion database in sync. Runs as a single command — no manual data entry.

```
Gmail → fetch → LLM classify → ETL → Notion
```

---

## Setup

**Requirements:** Docker · Python 3.8+ · Gmail account · Notion workspace

### 1. Clone the repo

```bash
git clone https://github.com/shraddhabhandarkar3/emli.git
cd emli
make setup   # creates .env from .env.example
```

### 2. Gmail credentials

In [Google Cloud Console](https://console.cloud.google.com): enable the Gmail API, create an OAuth 2.0 Desktop client, download the JSON, and save it as `client_secret.json` in the project root.

Install dependencies and run the one-time OAuth flow (opens a browser):

```bash
pip install -r requirements.txt
make auth
```

### 3. Notion

**Create an integration:**
1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**
2. Give it a name (e.g. "emli"), select your workspace, hit Submit
3. Under **Capabilities**, enable Read content, Insert content, and Update content
4. Copy the **Internal Integration Token** — paste it as `NOTION_TOKEN` in `.env`

**Create a page and share it:**
1. In Notion, create a new page anywhere in your workspace (this is where the Applications database will live)
2. Click **...** (top-right of the page) → **Connections** → find and connect your integration
3. Copy the page ID from the URL:

```
https://www.notion.so/myworkspace/My-Page-3d6b23c4f5e74a89b12cd34ef5678901
                                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                             this is your NOTION_PAGE_ID
```

It's the 32-character hex string at the end of the URL (ignore any dashes).

### 4. Fill in `.env`

```bash
NOTION_TOKEN=secret_...      # Internal Integration Token from Step 3
NOTION_PAGE_ID=3d6b23c4...   # 32-char page ID from Step 3
API_KEY=gsk_...              # Groq API key — free at console.groq.com
                             # (or see LLM Options below for other providers)
```

### 5. Create the Notion database

```bash
make setup-notion
```

This creates the Applications database inside your Notion page with all required columns and writes `NOTION_DATABASE_ID` to your `.env` automatically. You should see:

```
✓  Applications database created.
   NOTION_DATABASE_ID=... written to .env
```

Running it again confirms everything is configured:

```
✓  Notion already configured.
```

### 6. Build the Docker image

```bash
make build
```

### 7. Run

> **Default: API mode (Groq).** The pipeline uses an external LLM by default — fast, accurate, and free within Groq's daily limits. If you'd prefer to keep your data fully private and have a capable machine (8 GB+ RAM), you can switch to a local model with Ollama instead. See [LLM options](#llm-options) below.

```bash
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

Docker only needs to be running when the cron fires — you can close it after the pipeline finishes. On macOS, enable **"Start Docker Desktop when you log in"** in Docker Desktop → Settings so it's always ready.

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
| `make auth` | Gmail OAuth (run once) |
| `make setup-notion` | Create Notion database and write `NOTION_DATABASE_ID` to `.env` |
| `make build` | Build the pipeline Docker image |
| `make pipeline-docker` | Run full pipeline once in Docker |
| `make schedule` | Start pipeline on a recurring schedule (background) |
| `make unschedule` | Stop the scheduled pipeline |
| `make logs` | Follow container logs |
| `make pipeline` | Run full pipeline locally (requires venv) |
| `make up` / `make down` | Start / stop infrastructure |
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
  notion_sync/  applications → Notion (upsert)
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
5. Notion sync upserts each application row into the Applications database

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make up        # start Postgres + Ollama
make pipeline  # run locally against Docker infra
```
