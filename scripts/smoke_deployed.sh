#!/usr/bin/env bash
set -eo pipefail

TARGET_URL="${1:-http://localhost:8000}"
TARGET_URL="${TARGET_URL%/}"

echo "=================================================="
echo "Smoke Testing Deployed App: $TARGET_URL"
echo "=================================================="

FAILED=0

# Helper to print colored status
pass() {
    echo -e "  [\033[0;32mPASS\033[0m] $1"
}

fail() {
    echo -e "  [\033[0;31mFAIL\033[0m] $1: $2"
    FAILED=$((FAILED + 1))
}

# 1. Health check
echo -n "Checking /health ... "
HEALTH_OUT=$(curl -sS -w "\n%{http_code}" "$TARGET_URL/health" 2>&1 || true)
HTTP_CODE=$(echo "$HEALTH_OUT" | tail -n 1)
BODY=$(echo "$HEALTH_OUT" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    echo ""
    pass "Endpoint /health returned 200 OK ($BODY)"
else
    echo ""
    fail "Endpoint /health" "Expected 200 with status ok, got code $HTTP_CODE: $BODY"
fi

# 2. Version check
echo -n "Checking /version ... "
VERSION_OUT=$(curl -sS -w "\n%{http_code}" "$TARGET_URL/version" 2>&1 || true)
HTTP_CODE=$(echo "$VERSION_OUT" | tail -n 1)
BODY=$(echo "$VERSION_OUT" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | grep -q 'git_sha' && echo "$BODY" | grep -q 'python_version'; then
    echo ""
    pass "Endpoint /version returned 200 with git_sha & environment details"
    echo "         Version Info: $BODY"
else
    echo ""
    fail "Endpoint /version" "Expected 200 with version metadata, got code $HTTP_CODE: $BODY"
fi

# 3. Replay endpoint check
echo -n "Checking /replay/sample_demo ... "
REPLAY_OUT=$(curl -sS -N --max-time 5 "$TARGET_URL/replay/sample_demo?speed=100" 2>&1 | head -n 15 || true)

if echo "$REPLAY_OUT" | grep -q 'replay_meta'; then
    echo ""
    pass "Endpoint /replay/sample_demo successfully streamed SSE events"
else
    echo ""
    fail "Endpoint /replay/sample_demo" "Expected SSE stream with replay_meta, got: $REPLAY_OUT"
fi

# 4. Demo runs list check
echo -n "Checking /demo_runs ... "
DEMO_RUNS_OUT=$(curl -sS -w "\n%{http_code}" "$TARGET_URL/demo_runs" 2>&1 || true)
HTTP_CODE=$(echo "$DEMO_RUNS_OUT" | tail -n 1)
BODY=$(echo "$DEMO_RUNS_OUT" | sed '$d')

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | grep -q 'sample_demo'; then
    echo ""
    pass "Endpoint /demo_runs listed available demo runs"
else
    echo ""
    fail "Endpoint /demo_runs" "Expected 200 with sample_demo, got code $HTTP_CODE: $BODY"
fi

echo "=================================================="
if [ "$FAILED" -eq 0 ]; then
    echo -e "SMOKE TEST RESULT: \033[0;32mALL CHECKS PASSED\033[0m"
    exit 0
else
    echo -e "SMOKE TEST RESULT: \033[0;31m$FAILED CHECKS FAILED\033[0m"
    exit 1
fi
