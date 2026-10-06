import subprocess
import time
import pytest
from playwright.sync_api import sync_playwright
import sqlite3
import os

def test_smoke():
    # Seed DBs
    subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
    
    # Reset chaos
    if os.path.exists("chaos_state.json"):
        os.remove("chaos_state.json")
    
    # Start apps
    vp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
    erp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.erp.main:app", "--port", "8002"])
    
    time.sleep(2)
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            
            # Vendor Portal
            page = browser.new_page()
            page.goto("http://localhost:8001")
            page.fill("#username", "admin")
            page.fill("#password", "password123")
            page.click("#login-btn")
            
            page.wait_for_selector("#invoice-list")
            assert "INV-100" in page.content()
            
            page.click("#view-INV-100")
            page.wait_for_selector("#inv-amount")
            amount = page.locator("#inv-amount").inner_text()
            assert amount == "500.0"
            
            # ERP
            page2 = browser.new_page()
            page2.goto("http://localhost:8002")
            page2.fill("#username", "admin")
            page2.fill("#password", "admin")
            page2.click("#login-btn")
            
            page2.wait_for_selector("#add-bill-link")
            page2.click("#add-bill-link")
            
            page2.wait_for_selector("#invoice_id")
            page2.fill("#invoice_id", "INV-100")
            page2.fill("#vendor", "Acme Corp")
            page2.fill("#amount", amount)
            page2.fill("#due_date", "2024-11-01")
            page2.click("#submit-btn")
            
            page2.wait_for_selector("#success-msg")
            assert "successfully" in page2.content()
            
            browser.close()
            
            # API Verification
            import requests
            resp = requests.get("http://localhost:8002/api/verify/INV-100")
            assert resp.status_code == 200
            assert resp.json()["amount"] == 500.0
            print("Smoke test passed successfully!")
            
    finally:
        vp_proc.terminate()
        erp_proc.terminate()
        vp_proc.wait()
        erp_proc.wait()

if __name__ == "__main__":
    test_smoke()
