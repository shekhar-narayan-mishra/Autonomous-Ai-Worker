import asyncio
import subprocess
import pytest
from playwright.async_api import async_playwright
import httpx

@pytest.mark.ui
@pytest.mark.asyncio
async def test_ui_regression():
    server_process = None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/")
            if resp.status_code != 200:
                server_process = subprocess.Popen(["venv/bin/uvicorn", "server:app", "--port", "8000"])
    except Exception:
        server_process = subprocess.Popen(["venv/bin/uvicorn", "server:app", "--port", "8000"])

    try:
        # Wait for server to start
        for _ in range(30):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://localhost:8000/")
                    if resp.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.5)
        else:
            pytest.fail("Server did not start in time")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("http://localhost:8000/")
            
            # Wait for UI to load
            await page.wait_for_selector("#run-btn")
            
            # 1. A new run clears a previous terminal result
            await page.evaluate("""
                document.getElementById('final-summary').innerHTML = 'OLD RESULT';
                document.querySelector('#verifier-table tbody').innerHTML = '<tr><td>OLD</td></tr>';
                document.getElementById('final-panel').style.display = 'block';
            """)
            # Mock fetch to not actually run a task
            await page.evaluate("""
                window.fetch = async () => ({
                    json: async () => ({ run_id: "test-run-id" })
                });
                window.EventSource = function() {
                    this.close = () => {};
                    this.onerror = null;
                    this.onmessage = null;
                };
            """)
            await page.evaluate("startRun()")
            
            summary_html = await page.evaluate("document.getElementById('final-summary').innerHTML")
            assert summary_html == "", "Final summary should be cleared on new run"
            
            tbody_html = await page.evaluate("document.querySelector('#verifier-table tbody').innerHTML")
            assert tbody_html == "", "Verifier table should be cleared on new run"
            
            # 2. A backend startup failure is shown as an error
            # Simulate a "final" event with error
            await page.evaluate("""
                let es = new EventSource('/stream/test-run-id');
                // runTask overrides eventSource global
                eventSource = es;
                eventSource.onmessage({
                    data: JSON.stringify({ type: 'final', data: { result: 'error', error: 'Database locked' } })
                });
            """)
            
            is_visible = await page.evaluate("document.getElementById('final-panel').style.display === 'block'")
            assert is_visible, "Final panel should be visible after startup error"
            
            summary_text = await page.evaluate("document.getElementById('final-summary').innerText")
            assert "Database locked" in summary_text or "STARTUP_ERROR" in summary_text, f"Expected STARTUP_ERROR, got {summary_text}"
            
            # 3. An event-stream disconnect is handled honestly
            await page.evaluate("""
                eventSource.onerror(new Error('Network error'));
            """)
            summary_text2 = await page.evaluate("document.getElementById('final-summary').innerText")
            assert "CONNECTION_LOST" in summary_text2, f"Expected CONNECTION_LOST, got {summary_text2}"
            
            await browser.close()
    finally:
        if server_process:
            server_process.terminate()
            server_process.wait()

