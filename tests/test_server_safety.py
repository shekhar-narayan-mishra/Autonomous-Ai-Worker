import asyncio
import os
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from server import app, _reset_safety_state


@pytest.fixture(autouse=True)
def reset_server_state():
    _reset_safety_state()
    yield
    _reset_safety_state()


def test_run_token_required_and_enforced():
    client = TestClient(app)
    with patch.dict(os.environ, {"RUN_TOKEN": "secret-pass-123", "DEMO_MODE": ""}):
        # 1. Missing token -> 403
        r1 = client.post("/run", json={"task": "test", "auto_approve": True})
        assert r1.status_code == 403
        assert "run token" in r1.json()["detail"].lower()

        # 2. Incorrect token -> 403
        r2 = client.post("/run", json={"task": "test", "run_token": "wrong-token"})
        assert r2.status_code == 403

        # 3. Valid token in JSON body -> 200
        r3 = client.post("/run", json={"task": "test", "run_token": "secret-pass-123"})
        assert r3.status_code == 200
        assert "run_id" in r3.json()


def test_run_token_in_header():
    client = TestClient(app)
    with patch.dict(os.environ, {"RUN_TOKEN": "header-token-abc", "DEMO_MODE": ""}):
        # Header X-Run-Token -> 200
        r = client.post("/run", json={"task": "test"}, headers={"X-Run-Token": "header-token-abc"})
        assert r.status_code == 200
        assert "run_id" in r.json()


def test_demo_mode_replay_only_returns_403():
    client = TestClient(app)
    # When RUN_TOKEN is unset and DEMO_MODE=replay_only -> 403
    with patch.dict(os.environ, {"RUN_TOKEN": "", "DEMO_MODE": "replay_only"}):
        r = client.post("/run", json={"task": "test"})
        assert r.status_code == 403
        assert "replay-only" in r.json()["detail"].lower()


def test_rate_limit_active_run():
    client = TestClient(app)
    with patch.dict(os.environ, {"RUN_TOKEN": "", "DEMO_MODE": ""}):
        # Set an active run in server state
        import server
        server.active_run_id = "already-running-id"

        r = client.post("/run", json={"task": "test"})
        assert r.status_code == 429
        assert "currently active" in r.json()["detail"].lower()


def test_rate_limit_max_5_per_hour():
    client = TestClient(app)
    with patch.dict(os.environ, {"RUN_TOKEN": "", "DEMO_MODE": ""}):
        import server
        # Reset active_run_id between calls to simulate sequential runs from same IP
        for i in range(5):
            server.active_run_id = None
            r = client.post("/run", json={"task": f"test_{i}"})
            assert r.status_code == 200, f"Run {i+1} should succeed"

        server.active_run_id = None
        # 6th run from same client IP should fail with 429
        r6 = client.post("/run", json={"task": "test_6"})
        assert r6.status_code == 429
        assert "maximum 5 runs per hour" in r6.json()["detail"].lower()


def test_demo_runs_replay():
    client = TestClient(app)
    # GET /replay/sample_demo should stream trace from demo_runs/
    res = client.get("/replay/sample_demo?speed=100")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    text = res.text
    assert "replay_meta" in text
    assert "sample_demo" in text
