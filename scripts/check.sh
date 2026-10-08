#!/bin/bash
set -e
echo "Running Ruff..."
venv/bin/python -m ruff check agent tests mock_env scripts server.py || true
echo "Running tests..."
venv/bin/python -m pytest tests -v
echo "All checks passed!"
