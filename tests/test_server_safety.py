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
    # sample_demo must end in failed status since it did not pass verifier
    assert '"result": "failed"' in text


def test_health_and_version_endpoints():
    client = TestClient(app)
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_ver = client.get("/version")
    assert r_ver.status_code == 200
    v = r_ver.json()
    assert "git_sha" in v
    assert "python_version" in v
    assert "packages" in v
    assert "providers" in v
    # Check that secrets are NEVER in version
    v_str = str(v)
    assert "AIza" not in v_str
    assert "gsk_" not in v_str
    assert "sk-or" not in v_str


def test_demo_runs_metadata_and_failed_run_marking():
    client = TestClient(app)
    r = client.get("/demo_runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) >= 1
    sample = next((x for x in runs if x["id"] == "sample_demo"), None)
    assert sample is not None
    assert sample["steps"] == 9
    assert sample["finish_called"] is False
    assert sample["is_passing"] is False
    assert sample["status"] == "failed"
    assert "Failed run" in sample["label"]


def test_replay_only_mode_with_zero_sdks_installed():
    client = TestClient(app)
    orig_import = __import__

    def fake_import(name, *args, **kwargs):
        if "google" in name or "groq" in name:
            raise ImportError(f"No module named '{name}' (simulated missing SDK)")
        return orig_import(name, *args, **kwargs)

    with patch.dict(os.environ, {"DEMO_MODE": "replay_only", "RUN_TOKEN": ""}):
        with patch("builtins.__import__", side_effect=fake_import):
            # 1. Root UI page loads with zero SDKs
            r_root = client.get("/")
            assert r_root.status_code == 200

            # 2. /health returns 200
            r_health = client.get("/health")
            assert r_health.status_code == 200
            assert r_health.json()["demo_mode"] is True

            # 3. /version returns 200 with import_ok=False for missing SDKs
            r_ver = client.get("/version")
            assert r_ver.status_code == 200
            v_data = r_ver.json()
            assert v_data["demo_mode"] == "replay_only"
            assert v_data["providers"]["gemini"]["import_ok"] is False
            assert v_data["providers"]["groq"]["import_ok"] is False

            # 4. /env returns 200 and skips chain/preflight
            r_env = client.get("/env")
            assert r_env.status_code == 200
            assert r_env.json()["effective_config"]["chain_order"] == []

            # 5. POST /run returns 403
            r_run = client.post("/run", json={"task": "test"})
            assert r_run.status_code == 403
            assert "replay-only" in r_run.json()["detail"].lower()

            # 6. Replay streams successfully and concludes as failed run
            r_replay = client.get("/replay/sample_demo?speed=100")
            assert r_replay.status_code == 200
            assert "sample_demo" in r_replay.text
            assert '"result": "failed"' in r_replay.text

