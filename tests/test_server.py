import pytest
from fastapi.testclient import TestClient
from server import app
import os
import json

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200

def test_env():
    response = client.get("/env")
    assert response.status_code == 200
    assert "apps" in response.json()

def test_chaos():
    response = client.post("/chaos", json={"flag": "test_flag"})
    assert response.status_code == 200
    assert os.path.exists("chaos_state.json")
    with open("chaos_state.json") as f:
        assert json.load(f)["test_flag"] is True

def test_run():
    response = client.post("/run", json={"task": "test", "auto_approve": True})
    assert response.status_code == 200
    assert "run_id" in response.json()
