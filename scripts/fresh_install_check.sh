#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

TMP_VENV=$(mktemp -d /tmp/fresh_venv_XXXXXX)
echo "=== 1. Creating fresh venv at $TMP_VENV ==="
python3 -m venv "$TMP_VENV"

cleanup() {
    echo "=== Cleaning up $TMP_VENV ==="
    rm -rf "$TMP_VENV"
}
trap cleanup EXIT

echo "=== 2. Installing dependencies from requirements.lock.txt ==="
"$TMP_VENV/bin/pip" install --quiet --no-cache-dir -r requirements.lock.txt

echo "=== 3. Testing Provider SDK imports and Client constructions ==="
"$TMP_VENV/bin/python" - << 'EOF'
import sys

# 1. Google GenAI
try:
    from google import genai
    c_gemini = genai.Client(api_key="test-gemini-key")
    print("✓ google-genai imported & Client constructed successfully")
except Exception as e:
    print(f"✗ Failed google-genai: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Groq
try:
    import groq
    from groq import Groq, AsyncGroq
    c_groq = Groq(api_key="gsk_test")
    c_agroq = AsyncGroq(api_key="gsk_test")
    print("✓ groq imported & Groq/AsyncGroq constructed successfully")
except Exception as e:
    print(f"✗ Failed groq: {e}", file=sys.stderr)
    sys.exit(1)

# 3. HTTPX
try:
    import httpx
    c_httpx = httpx.AsyncClient(timeout=10)
    print("✓ httpx imported & AsyncClient(timeout=10) constructed successfully")
except Exception as e:
    print(f"✗ Failed httpx: {e}", file=sys.stderr)
    sys.exit(1)

# 4. Playwright, FastAPI, Pydantic, PyYAML
try:
    import playwright
    import fastapi
    import uvicorn
    import pydantic
    import yaml
    print("✓ playwright, fastapi, uvicorn, pydantic, pyyaml imported successfully")
except Exception as e:
    print(f"✗ Failed core libraries: {e}", file=sys.stderr)
    sys.exit(1)

print("All SDK imports and client constructions passed!")
EOF

echo "=== 4. Running pytest with fresh venv ==="
"$TMP_VENV/bin/python" -m pytest tests -v

echo "=== Fresh install check completed successfully! ==="
