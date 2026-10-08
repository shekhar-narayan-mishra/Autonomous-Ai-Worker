import asyncio
import os

from playwright.async_api import async_playwright

from agent.tools.browser import BrowserContext


async def main():
    os.system("venv/bin/python mock_env/seed.py")
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context()
        page = await ctx.new_page()
        
        await page.goto("http://localhost:8002/")
        await page.fill("#username", "admin")
        await page.fill("#password", "admin")
        await page.click("#login-btn")
        
        await page.wait_for_url("**/dashboard")
        
        await page.goto("http://localhost:8002/add_bill")
        await page.wait_for_selector("#submit-btn")
        
        BrowserContext.page = page
        snap = await BrowserContext.snapshot()
        tokens = len(snap.split())
        
        with open("docs/snapshot_addbill.txt", "w") as f:
            f.write(snap)
            f.write(f"\n\nTokens: {tokens}")
            
        print("Snapshot captured.")
        await b.close()

if __name__ == "__main__":
    asyncio.run(main())
