#!/usr/bin/env bash
set -e

echo "=== Installing dependencies from requirements.lock.txt ==="
pip install --no-cache-dir -r requirements.lock.txt

echo "=== Installing Playwright Chromium browser with dependencies ==="
playwright install --with-deps chromium

echo "=== Seeding mock databases ==="
python mock_env/seed.py

echo "=== Render build finished successfully ==="
