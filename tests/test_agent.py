
import pytest
from pydantic import BaseModel

from agent.loop import run_loop
from agent.tools.base import BaseTool, RiskLevel, ToolResult, registry
from agent.tools.browser import BrowserContext


class DummyArgs(BaseModel):
    foo: str

class DummyTool(BaseTool):
    name = "dummy"
    description = "dummy tool"
    args_schema = DummyArgs
    risk_level = RiskLevel.READ
    async def run(self, foo: str):
        return ToolResult(ok=True, observation=foo)

def test_registry():
    registry.register(DummyTool())
    t = registry.get("dummy")
    assert t is not None
    assert t.name == "dummy"
    sp = registry.get_system_prompt_segment()
    assert "dummy" in sp

@pytest.mark.asyncio
async def test_browser_snapshot():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context()
        page = await ctx.new_page()
        await page.set_content("<button>Click Me</button>")
        BrowserContext.page = page
        snap = await BrowserContext.snapshot()
        assert "Click Me" in snap
        assert "[e1]" in snap
        await b.close()

@pytest.mark.asyncio
async def test_loop_with_fake_llm(monkeypatch):
    import agent.loop
    from agent.llm import LLMResponse
    
    calls = 0
    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(thought="t", action="dummy", args={"foo": "bar"}, expected_outcome="o"), {"tokens": {}, "latency_ms": 0}
        else:
            return LLMResponse(thought="t2", action="finish", args={"summary": "done", "evidence": "something"}, expected_outcome="o2"), {"tokens": {}, "latency_ms": 0}
            
    monkeypatch.setattr(agent.loop, "generate_action", fake_generate)
    
    await run_loop("fake task")
