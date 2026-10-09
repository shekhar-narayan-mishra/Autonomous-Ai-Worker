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

# Kill any existing instances on port 8000
echo "Cleaning up any existing server on port 8000..."
PIDS=$(lsof -t -i:8000 || true)
if [ ! -z "$PIDS" ]; then
    echo "Killing processes on port 8000..."
    kill -9 $PIDS
fi

# Start server
echo "Starting Server UI (Port 8000)..."
venv/bin/uvicorn server:app --port 8000 > /dev/null 2>&1 &
SERVER_PID=$!

# Wait and Health Check
echo "Waiting for UI service to be healthy..."
MAX_RETRIES=15
RETRY=0
HEALTHY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:8000 > /dev/null; then
        HEALTHY=1
        break
    fi
    sleep 1
    RETRY=$((RETRY+1))
done
if [ $HEALTHY -eq 0 ]; then
    echo "FATAL: Service on port 8000 failed to start."
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

echo "All services are running and healthy!"
echo "Opening UI in browser..."

if which open > /dev/null; then
    open http://localhost:8000/
elif which xdg-open > /dev/null; then
    xdg-open http://localhost:8000/
fi

echo "Press Ctrl+C to stop all servers."

# Keep script running and handle graceful shutdown
trap "echo 'Shutting down...'; kill -9 $SERVER_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
