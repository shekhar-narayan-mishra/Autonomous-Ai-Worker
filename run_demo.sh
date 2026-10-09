#!/usr/bin/env bash
set -e

echo "Setting up Autonomous AI Task Worker Demo..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

echo "Checking required tools..."
venv/bin/python -c "
import sys
try:
    from agent.tools.base import registry
    import agent.memory  # registers memory tools
    import agent.tools.browser # registers browser tools
    import agent.tools.human # registers ask_human
    import agent.tools.finish # registers finish
except ImportError:
    print('Failed to import tool registry.')
    sys.exit(1)
required = ['browser', 'ask_human', 'save_fact', 'recall', 'finish']
tool_names = [t.name for t in registry.get_all()]
missing = [r for r in required if r not in tool_names]
if missing:
    print(f'FATAL: Missing essential tools: {missing}')
    sys.exit(1)
print(f'Registered tools OK: {tool_names}')
" || exit 1

# Seed the DB
echo "Seeding databases..."
venv/bin/python mock_env/seed.py

# Kill any existing instances on ports 8000-8002
echo "Cleaning up any existing servers on ports 8000, 8001, 8002..."
for port in 8000 8001 8002; do
    PIDS=$(lsof -t -i:$port || true)
    if [ ! -z "$PIDS" ]; then
        echo "Killing processes on port $port..."
        kill -9 $PIDS
    fi
done

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

# Wait and Health Check
echo "Waiting for services to be healthy..."
for port in 8000 8001 8002; do
    MAX_RETRIES=15
    RETRY=0
    HEALTHY=0
    while [ $RETRY -lt $MAX_RETRIES ]; do
        if curl -s http://localhost:$port > /dev/null; then
            HEALTHY=1
            break
        fi
        sleep 1
        RETRY=$((RETRY+1))
    done
    if [ $HEALTHY -eq 0 ]; then
        echo "FATAL: Service on port $port failed to start."
        kill $VP_PID $ERP_PID $SERVER_PID 2>/dev/null || true
        exit 1
    fi
done

echo "All services are running and healthy!"
echo "Opening UI in browser..."

if which open > /dev/null; then
    open http://localhost:8000/
elif which xdg-open > /dev/null; then
    xdg-open http://localhost:8000/
fi

echo "Press Ctrl+C to stop all servers."

# Keep script running and handle graceful shutdown
trap "echo 'Shutting down...'; kill -9 $VP_PID $ERP_PID $SERVER_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
