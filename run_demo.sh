#!/usr/bin/env bash
set -e

echo "Setting up Autonomous AI Task Worker Demo..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Seed the DB
echo "Seeding databases..."
venv/bin/python mock_env/seed.py

# Kill any existing instances
echo "Cleaning up any existing servers..."
pkill -f uvicorn || true

# Start servers
echo "Starting Vendor Portal (Port 8001)..."
venv/bin/uvicorn mock_env.vendor_portal.main:app --port 8001 > /dev/null 2>&1 &
VP_PID=$!

echo "Starting ERP (Port 8002)..."
venv/bin/uvicorn mock_env.erp.main:app --port 8002 > /dev/null 2>&1 &
ERP_PID=$!

echo "Starting Server UI (Port 8000)..."
venv/bin/uvicorn server:app --port 8000 > /dev/null 2>&1 &
SERVER_PID=$!

# Wait for servers to be ready
sleep 3
echo "Servers are running!"
echo "Opening UI in browser..."

if which open > /dev/null; then
    open http://localhost:8000/
elif which xdg-open > /dev/null; then
    xdg-open http://localhost:8000/
fi

echo "Press Ctrl+C to stop all servers."

# Keep script running and handle graceful shutdown
trap "echo 'Shutting down...'; kill $VP_PID $ERP_PID $SERVER_PID; exit 0" SIGINT SIGTERM
wait
