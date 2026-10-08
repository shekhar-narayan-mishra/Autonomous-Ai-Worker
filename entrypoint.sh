#!/usr/bin/env bash
set -e

echo "[Deploy] Initializing mock databases..."
python mock_env/seed.py

echo "[Deploy] Starting Vendor Portal on internal port 8001..."
uvicorn mock_env.vendor_portal.main:app --host 127.0.0.1 --port 8001 &
VP_PID=$!

echo "[Deploy] Starting Internal ERP on internal port 8002..."
uvicorn mock_env.erp.main:app --host 127.0.0.1 --port 8002 &
ERP_PID=$!

# Wait for internal services to come up
sleep 2

# Handle graceful shutdown of background services
cleanup() {
    echo "[Deploy] Shutting down background services..."
    kill "$VP_PID" "$ERP_PID" 2>/dev/null || true
    wait "$VP_PID" "$ERP_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "[Deploy] Starting Main UI & Server on port ${PORT:-8000}..."
uvicorn server:app --host 0.0.0.0 --port "${PORT:-8000}"
