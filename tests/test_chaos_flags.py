import httpx
import pytest

from agent.reliability import ErrorClassification, ReliabilityManager
from mock_env import chaos


@pytest.mark.asyncio
async def test_chaos_flags_behavior():
    rm = ReliabilityManager()

    # Test slow_load
    chaos.set_chaos({"slow_load": True})
    async with httpx.AsyncClient() as client:
        import time
        start = time.time()
        r = await client.get("http://localhost:8001/")
        assert time.time() - start >= 2.0
    chaos.set_chaos({"slow_load": False})

    # Test flaky_500
    chaos.set_chaos({"flaky_500": True})
    # Loop until 500 since we can't monkeypatch a background process
    async with httpx.AsyncClient() as client:
        got_500 = False
        for _ in range(15):
            r = await client.get("http://localhost:8001/")
            if r.status_code == 500:
                got_500 = True
                break
        assert got_500
            
    # Classify generic HTTP 500 equivalent in Playwright (which raises an Error)
    err = Exception("net::ERR_HTTP_RESPONSE_CODE_FAILURE")
    assert rm.classify_error(err) == ErrorClassification.TRANSIENT
    chaos.set_chaos({"flaky_500": False})
    
    # Test random_popup_modal
    chaos.set_chaos({"random_popup_modal": True})
    async with httpx.AsyncClient() as client:
        r = await client.get("http://localhost:8001/invoices", cookies={"session": "valid_token"})
        assert "annoying-modal" in r.text
    err = Exception("waiting for selector \"[data-aid='e1']\" to be visible")
    assert rm.classify_error(err) == ErrorClassification.WRONG_APPROACH
    chaos.set_chaos({"random_popup_modal": False})

    # Test expired_session
    chaos.set_chaos({"expired_session": True})
    async with httpx.AsyncClient() as client:
        r = await client.get("http://localhost:8001/invoices", cookies={"session": "valid_token"})
        # Should redirect to login
        assert r.status_code == 302
        assert "/" == r.headers.get("location")
    err = Exception("Element is not attached to the DOM")
    assert rm.classify_error(err) == ErrorClassification.WRONG_APPROACH
    chaos.set_chaos({"expired_session": False})

    # Test validation_error_on_first_submit in ERP
    chaos.set_chaos({"validation_error_on_first_submit": True})
    async with httpx.AsyncClient() as client:
        r = await client.post("http://localhost:8002/add_bill", cookies={"erp_session": "valid_token"}, data={"invoice_id": "INV-101", "vendor": "Acme", "amount": "100", "due_date": "2023-11-01", "notes": ""}, follow_redirects=False)
        assert r.status_code == 302
        assert "System Validation Failed" in r.headers.get("location", "").replace("%20", " ")
    err = Exception("waiting for locator(\"[data-aid='e6']\")") # timeout finding something after submit
    assert rm.classify_error(err) == ErrorClassification.WRONG_APPROACH
    chaos.set_chaos({"validation_error_on_first_submit": False})

    # Test missing_field in ERP
    chaos.set_chaos({"missing_field": True})
    async with httpx.AsyncClient() as client:
        r = await client.post("http://localhost:8002/add_bill", cookies={"erp_session": "valid_token"}, data={"invoice_id": "", "vendor": "Acme", "amount": "100", "due_date": "2023-11-01", "notes": ""}, follow_redirects=False)
        assert r.status_code == 302
        assert "Invoice ID is required" in r.headers.get("location", "").replace("%20", " ")
    
    # When field is missing, agent tries to submit and gets an error, then retries. But if it times out waiting for something:
    err = Exception("waiting for locator(\"[data-aid='e1']\")")
    assert rm.classify_error(err) == ErrorClassification.WRONG_APPROACH
    chaos.set_chaos({"missing_field": False})
