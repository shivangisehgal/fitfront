#!/bin/bash
# FitFront — One-command startup
# Starts tunnel, captures URL, updates .env, starts backend

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"

echo "=========================================="
echo "  FitFront — Startup"
echo "=========================================="

# ── Detect LOCAL_CHAT_MODE from .env ──
LOCAL_CHAT_MODE=$(grep -E "^LOCAL_CHAT_MODE=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]')
LOCAL_CHAT_MODE=${LOCAL_CHAT_MODE:-false}

TUNNEL_URL=""
TUNNEL_PID=""
TUNNEL_LOG=""

if [ "$LOCAL_CHAT_MODE" = "true" ]; then
    # ── LOCAL_CHAT_MODE: skip tunnel entirely ──
    echo ""
    echo "→ LOCAL_CHAT_MODE=true — skipping tunnel (no voice webhooks needed)"
    TUNNEL_URL="http://localhost:8000"

    # Make sure .env reflects localhost
    if grep -q "^SERVER_BASE_URL=" "$ENV_FILE"; then
        sed -i.bak "s|^SERVER_BASE_URL=.*|SERVER_BASE_URL=$TUNNEL_URL|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
    fi
    echo "✓ SERVER_BASE_URL=$TUNNEL_URL"
else
    # ── 1. Start localtunnel in background and capture the URL ──
    echo ""
    echo "→ Starting localtunnel..."

    TUNNEL_LOG=$(mktemp)

    npx localtunnel --port 8000 > "$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    # Wait up to 15s for localtunnel to print the URL
    for i in $(seq 1 30); do
        if ! kill -0 $TUNNEL_PID 2>/dev/null; then
            echo "  ✗ localtunnel exited unexpectedly."
            echo "  Output: $(cat "$TUNNEL_LOG" 2>/dev/null)"
            rm -f "$TUNNEL_LOG"
            exit 1
        fi
        if grep -q "your url is:" "$TUNNEL_LOG" 2>/dev/null; then
            TUNNEL_URL=$(grep -oE "https://[a-zA-Z0-9._-]+\.loca\.lt" "$TUNNEL_LOG" | head -1)
            break
        fi
        sleep 0.5
    done

    if [ -z "$TUNNEL_URL" ]; then
        echo ""
        echo "✗ Failed to get localtunnel URL (timeout after 15s)."
        echo "  Output: $(cat "$TUNNEL_LOG" 2>/dev/null)"
        echo ""
        echo "Common causes:"
        echo "  • npx/node not installed or not in PATH"
        echo "  • Network issue reaching localtunnel server"
        echo ""
        echo "Tip: Set LOCAL_CHAT_MODE=true in .env to skip the tunnel entirely."
        kill $TUNNEL_PID 2>/dev/null || true
        rm -f "$TUNNEL_LOG"
        exit 1
    fi

    echo "✓ Tunnel running: $TUNNEL_URL (PID: $TUNNEL_PID)"

    # ── 2. Update .env with the new URL ──
    echo ""
    echo "→ Updating .env with tunnel URL..."

    if grep -q "^SERVER_BASE_URL=" "$ENV_FILE"; then
        sed -i.bak "s|^SERVER_BASE_URL=.*|SERVER_BASE_URL=$TUNNEL_URL|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
        echo "✓ SERVER_BASE_URL=$TUNNEL_URL"
    else
        echo "SERVER_BASE_URL=$TUNNEL_URL" >> "$ENV_FILE"
        echo "✓ Added SERVER_BASE_URL=$TUNNEL_URL"
    fi
fi

# ── 3. Activate virtual environment ──
echo ""
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "→ Activating virtual environment..."
    source "$SCRIPT_DIR/venv/bin/activate"
    echo "✓ venv activated"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "→ Activating virtual environment..."
    source "$SCRIPT_DIR/.venv/bin/activate"
    echo "✓ .venv activated"
fi

# ── 4. Start uvicorn ──
echo ""
echo "=========================================="
if [ "$LOCAL_CHAT_MODE" = "true" ]; then
    echo "  Mode:     LOCAL CHAT (no tunnel)"
    echo "  Backend:  http://localhost:8000"
    echo "  Chat:     http://localhost:5173"
    echo "  Docs:     http://localhost:8000/docs"
else
    echo "  Tunnel:   $TUNNEL_URL"
    echo "  Webhook:  $TUNNEL_URL/webhook/sms"
    echo "  LLM:      $TUNNEL_URL/api/llm"
    echo "  Docs:     $TUNNEL_URL/docs"
fi
echo "=========================================="
echo ""
echo "→ Starting backend (Ctrl+C to stop everything)..."
echo ""

# Trap Ctrl+C to kill tunnel (if running) and uvicorn
cleanup() {
    echo ""
    echo "→ Shutting down..."
    [ -n "$TUNNEL_PID" ] && kill $TUNNEL_PID 2>/dev/null && echo "✓ Tunnel stopped"
    [ -n "$TUNNEL_LOG" ] && rm -f "$TUNNEL_LOG"
    exit 0
}
trap cleanup INT TERM

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
UVICORN_PID=$!

# ── 5. Warm LLM in background so first call doesn't pay cold-start ──
LLM_PROVIDER=$(grep -E "^LLM_PROVIDER=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
LLM_PROVIDER=${LLM_PROVIDER:-ollama}

if [ "$LLM_PROVIDER" = "ollama" ]; then
    (
        sleep 3  # give uvicorn a moment to bind the port
        OLLAMA_MODEL=$(grep -E "^OLLAMA_MODEL=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
        OLLAMA_MODEL=${OLLAMA_MODEL:-llama3.2:latest}
        echo "→ Warming Ollama model: $OLLAMA_MODEL ..."
        if curl -sf -m 30 http://localhost:11434/v1/chat/completions \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"$OLLAMA_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}" \
            >/dev/null 2>&1; then
            echo "✓ Ollama warmed — first voice call will be fast"
        else
            echo "⚠ Ollama warm-up failed (is Ollama running on localhost:11434?). Backend will still work but the first call may be slow."
        fi
    ) &
elif [ "$LLM_PROVIDER" = "gemini" ]; then
    GEMINI_MODEL=$(grep -E "^GEMINI_MODEL=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    GEMINI_MODEL=${GEMINI_MODEL:-gemini-1.5-flash}
    echo "✓ Using Gemini API (model: $GEMINI_MODEL) — no warm-up needed"
fi

# Wait for either process to exit
wait $UVICORN_PID
cleanup
