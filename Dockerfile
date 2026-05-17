FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 (postgres driver)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Default entrypoint runs the full pipeline
ENTRYPOINT ["bash", "entrypoint.sh"]
