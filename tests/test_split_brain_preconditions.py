import os
import socket
import subprocess
import sys
import tempfile
import time
import httpx
import pytest
import yaml

from agent.env_manager import check_preconditions, get_app_base_url, reset_all_apps
from agent.llm import LLMResponse
from agent.loop import run_loop
from verifiers.invoice_entry import InvoiceEntryVerifier


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_servers():
    admin_token = "test-secret-admin-token-12345"
    p_port = get_free_port()
    e_port = get_free_port()

    env = os.environ.copy()
    env["ADMIN_TOKEN"] = admin_token

    # Start vendor portal on non-default port
    p_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_env.vendor_portal.main:app", "--port", str(p_port), "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    # Start ERP on non-default port
    e_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_env.erp.main:app", "--port", str(e_port), "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Wait for both to be up
    start_t = time.time()
    p_up = False
    e_up = False
    while time.time() - start_t < 10.0:
        with httpx.Client() as client:
            try:
                if not p_up and client.get(f"http://127.0.0.1:{p_port}/health").status_code == 200:
                    p_up = True
            except Exception:
                pass
            try:
                if not e_up and client.get(f"http://127.0.0.1:{e_port}/health").status_code == 200:
                    e_up = True
            except Exception:
                pass
        if p_up and e_up:
            break
        time.sleep(0.2)

    assert p_up and e_up, f"Mock servers failed to start on ports {p_port}, {e_port}"

    yield {
        "portal_port": p_port,
        "erp_port": e_port,
        "portal_url": f"http://127.0.0.1:{p_port}",
        "erp_url": f"http://127.0.0.1:{e_port}",
        "admin_token": admin_token,
    }

    p_proc.terminate()
    e_proc.terminate()
    p_proc.wait()
    e_proc.wait()


@pytest.fixture
def custom_env_config(mock_servers, monkeypatch):
    data = {
        "apps": [
            {
                "name": "Vendor Portal",
                "base_url": mock_servers["portal_url"],
                "credentials": {"username": "admin", "password": "password123"},
            },
            {
                "name": "Internal ERP",
                "base_url": mock_servers["erp_url"],
                "credentials": {"username": "admin", "password": "admin"},
            },
        ]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(data, f)
        temp_path = f.name

    monkeypatch.setenv("ENVIRONMENT_FILE", temp_path)
    monkeypatch.setenv("ADMIN_TOKEN", mock_servers["admin_token"])

    yield temp_path

    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_admin_endpoints_reject_missing_and_wrong_tokens(mock_servers):
    portal_url = mock_servers["portal_url"]
    erp_url = mock_servers["erp_url"]
    admin_token = mock_servers["admin_token"]

    with httpx.Client(timeout=5.0) as client:
        # 1. Missing token
        r1 = client.post(f"{portal_url}/admin/reset?profile=base")
        assert r1.status_code == 403
        r2 = client.post(f"{erp_url}/admin/reset?profile=base")
        assert r2.status_code == 403

        # 2. Wrong token
        r3 = client.post(f"{portal_url}/admin/reset?profile=base", headers={"X-Admin-Token": "invalid_token"})
        assert r3.status_code == 403
        r4 = client.post(f"{erp_url}/admin/reset?profile=base", headers={"X-Admin-Token": "invalid_token"})
        assert r4.status_code == 403

        # 3. Correct token
        r5 = client.post(f"{portal_url}/admin/reset?profile=base", headers={"X-Admin-Token": admin_token})
        assert r5.status_code == 200
        assert r5.json()["status"] == "ok"
        r6 = client.post(f"{erp_url}/admin/reset?profile=base", headers={"X-Admin-Token": admin_token})
        assert r6.status_code == 200
        assert r6.json()["status"] == "ok"


def test_reset_preconditions_and_verifier_hit_custom_ports(mock_servers, custom_env_config):
    portal_url = mock_servers["portal_url"]
    erp_url = mock_servers["erp_url"]

    # Verify URLs resolve to custom non-default ports
    assert get_app_base_url("Vendor Portal") == portal_url
    assert get_app_base_url("Internal ERP") == erp_url
    assert "8001" not in portal_url
    assert "8002" not in erp_url

    # 1. Reset all apps
    reset_all_apps("base")

    # 2. Preconditions check
    ok, msg, erp_bills = check_preconditions("base")
    assert ok is True, msg
    assert not any(b.get("invoice_id") == "INV-101" for b in erp_bills)

    # 3. Add bill to the custom ERP port
    with httpx.Client(timeout=5.0) as client:
        # Add bill for INV-101
        res = client.post(
            f"{erp_url}/add_bill",
            cookies={"erp_session": "valid_token"},
            data={
                "invoice_id": "INV-101",
                "vendor": "Acme Corp",
                "amount": "1200.50",
                "due_date": "2024-11-15",
                "notes": "",
            },
            follow_redirects=True,
        )
        assert res.status_code == 200

    # 4. Verifier should query custom ports and pass
    verifier = InvoiceEntryVerifier()
    v_res = verifier.verify({
        "vendor_name": "Acme Corp",
        "expected_outcome": "success",
        "initial_erp_bills": erp_bills,
    })
    assert v_res.passed is True
    assert v_res.evidence["portal_url"] == portal_url
    assert v_res.evidence["erp_url"] == erp_url
    assert v_res.evidence["bill_created_in_run"] is True


@pytest.mark.asyncio
async def test_env_reset_failed_aborts_before_step_1(mock_servers, custom_env_config, monkeypatch):
    erp_url = mock_servers["erp_url"]

    # Make reset fail by supplying an invalid ADMIN_TOKEN
    monkeypatch.setenv("ADMIN_TOKEN", "bad-token-causing-reset-failure")

    step_generated = []

    async def fake_generate_action(prompt, user_messages):
        step_generated.append(True)
        return LLMResponse(thought="Should not be called", action="finish", args={"summary": "done"}, expected_outcome="done"), {}

    monkeypatch.setattr("agent.loop.generate_action", fake_generate_action)

    res = await run_loop(
        "Enter invoice INV-101 into ERP",
        task_args={"reset_env": True, "seed_profile": "base", "verifier": "invoice_entry"},
        max_steps=5,
    )

    assert res["status"] == "env_reset_failed"
    assert res["passed"] is False
    assert len(step_generated) == 0, "Agent loop should abort before step 1!"
    # Must name the app and URL
    assert "Internal ERP" in res["report"] or "Vendor Portal" in res["report"]
    assert str(mock_servers["portal_port"]) in res["report"] or str(mock_servers["erp_port"]) in res["report"]


@pytest.mark.asyncio
async def test_duplicate_guard_repair_does_not_create_duplicate(mock_servers, custom_env_config, monkeypatch):
    erp_url = mock_servers["erp_url"]
    portal_url = mock_servers["portal_url"]
    admin_token = mock_servers["admin_token"]

    # Reset with duplicate_erp (which already has INV-101)
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{erp_url}/admin/reset?profile=duplicate_erp", headers={"X-Admin-Token": admin_token})

    # Verifier that fails on call 1 to force a repair attempt, then uses real InvoiceEntryVerifier
    from agent.verifier import CheckResult, VerifierResult
    verifier_calls = 0

    class RepairTestVerifier(InvoiceEntryVerifier):
        def verify(self, args):
            nonlocal verifier_calls
            verifier_calls += 1
            if verifier_calls == 1:
                return VerifierResult(
                    passed=False,
                    checks=[CheckResult(name="ERP check", passed=False, details=f"Initial check failed at {erp_url}")],
                    evidence={"erp_url": erp_url},
                )
            return super().verify(args)

    monkeypatch.setattr("agent.verifier._REGISTRY", {"invoice_entry": RepairTestVerifier})

    steps_called = 0

    async def scripted_generate(prompt, user_messages):
        nonlocal steps_called
        steps_called += 1
        # System prompt must include the duplicate guard rule
        assert "check the destination for an existing record with the same key" in prompt

        if steps_called == 1:
            # Step 1: Agent calls finish
            return LLMResponse(
                thought="Step 1 finish attempt",
                action="finish",
                args={"summary": "Invoice INV-101 entered into ERP", "evidence": f"{portal_url}/invoice/INV-101"},
                expected_outcome="done",
            ), {"provider": "fake", "tokens": {}, "latency_ms": 1}
        else:
            # Step 2: Repair attempt. Follows duplicate guard rule: checks destination, sees existing record,
            # does not create a duplicate bill; finishes stating it was already present.
            return LLMResponse(
                thought="Repair attempt: checking destination ERP for existing record with key INV-101. Found matching record, do not create another.",
                action="finish",
                args={
                    "summary": "Invoice INV-101 was already present in ERP with matching values, did not create duplicate",
                    "evidence": f"{erp_url}/dashboard",
                },
                expected_outcome="done",
            ), {"provider": "fake", "tokens": {}, "latency_ms": 1}

    monkeypatch.setattr("agent.loop.generate_action", scripted_generate)

    # Initial ERP bills snapshot has INV-101
    initial_bills = [
        {"invoice_id": "INV-100", "amount": 500.0, "due_date": "2024-10-01", "vendor": "Acme Corp"},
        {"invoice_id": "INV-101", "amount": 1200.5, "due_date": "2024-11-15", "vendor": "Acme Corp"},
    ]

    res = await run_loop(
        "Enter invoice INV-101 into ERP",
        task_args={
            "vendor_name": "Acme Corp",
            "verifier": "invoice_entry",
            "reset_env": False,
            "initial_erp_bills": initial_bills,
        },
        max_steps=5,
    )

    assert res["passed"] is True
    assert res["status"] == "SUCCESS_AFTER_REPAIR"
    assert res["evidence"]["bill_created_in_run"] is False

    # Check ERP bills count: exactly 1 bill for INV-101
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{erp_url}/api/bills")
        bills = r.json()
        inv101_bills = [b for b in bills if b.get("invoice_id") == "INV-101"]
        assert len(inv101_bills) == 1, "Duplicate bill must not have been created!"
