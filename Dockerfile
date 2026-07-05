# ── Backend-only (frontend deployed separately on Vercel) ─────────
FROM python:3.12-slim
WORKDIR /app

# Install system deps for asyncpg (needs libpq headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Railway injects PORT env var — uvicorn must bind to it
ENV PORT=8000
EXPOSE $PORT

CMD uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1
