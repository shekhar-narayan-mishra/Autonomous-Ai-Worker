import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock

from agent.llm import LLMResponse
from agent.loop import run_loop
from agent.tools.browser import BrowserTool, BrowserContext
from agent.tools.base import ToolResult, RiskLevel

class MockPage:
    def __init__(self, initial_url="about:blank"):
        self.url = initial_url
        self.goto_calls = []
        self.clicks = []
        self.fills = []
        self.evaluates = []
        self._password_count = 0
        self._err_text = ""
        self._screenshot_called = False
    
    async def goto(self, url):
        self.goto_calls.append(url)
        self.url = url
        
    async def wait_for_load_state(self, state):
        pass
        
    class Locator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector
            
        @property
        def first(self):
            return self
            
        async def fill(self, text):
            self.page.fills.append((self.selector, text))
            
        async def click(self):
            self.page.clicks.append(self.selector)
            
        async def count(self):
            if "password" in self.selector:
                return self.page._password_count
            return 0
            
    def locator(self, selector):
        return self.Locator(self, selector)
        
    async def evaluate(self, js):
        self.evaluates.append(js)
        # return snapshot mock if it's the snapshot js
        if "querySelectorAll" in js:
            return {"url": self.url, "title": "Mock", "elements": ["[e1] button 'btn'"], "text": "Mock text"}
        return self._err_text
        
    async def screenshot(self, path=None):
        self._screenshot_called = True


class LoopMockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.step = 0
        
    async def generate_action(self, prompt, messages):
        if self.step < len(self.responses):
            resp = self.responses[self.step]
        else:
            resp = LLMResponse(thought="finish", action="finish", args={"summary": "done"}, expected_outcome="done")
        self.step += 1
        return resp, {"provider": "mock", "tokens": {}, "latency_ms": 0}


@pytest.fixture
def mock_env(monkeypatch):
    import yaml
    env_data = {
        "apps": [
            {"name": "Vendor Portal", "base_url": "http://vp", "credentials": {"username": "v", "password": "v"}},
            {"name": "Internal ERP", "base_url": "http://erp", "credentials": {"username": "e", "password": "e"}}
        ]
    }
    import builtins
    orig_open = builtins.open
    def mock_open(path, *args, **kwargs):
        if "environment.yaml" in str(path):
            from io import StringIO
            return StringIO(yaml.dump(env_data))
        return orig_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", mock_open)


@pytest.mark.asyncio
async def test_target_aware_login(mock_env, monkeypatch):
    page = MockPage("http://vp")
    BrowserContext.page = page
    page._password_count = 1
    
    tool = BrowserTool()
    res = await tool.run(command="login", app="Internal ERP")
    
    # Should have navigated to http://erp because we started on http://vp
    assert page.goto_calls == ["http://erp"]
    assert res.ok is True
    assert "Logged in successfully" in res.observation
    assert ("input[type='password']", "e") in page.fills


@pytest.mark.asyncio
async def test_already_authenticated_login(mock_env, monkeypatch):
    page = MockPage("http://erp")
    BrowserContext.page = page
    page._password_count = 0  # No password fields = already authenticated
    
    tool = BrowserTool()
    res = await tool.run(command="login", app="Internal ERP")
    
    assert page.goto_calls == []  # Should not navigate since already on origin
    assert res.ok is True
    assert "already authenticated" in res.observation.lower()
    assert len(page.fills) == 0


@pytest.mark.asyncio
async def test_authentication_error_reported(mock_env, monkeypatch):
    page = MockPage("http://erp")
    BrowserContext.page = page
    page._password_count = 1
    page._err_text = "Invalid username or password"
    
    tool = BrowserTool()
    res = await tool.run(command="login", app="Internal ERP")
    
    assert res.ok is False
    assert "Login failed" in res.observation
    assert "Invalid username" in res.observation


@pytest.mark.asyncio
async def test_login_ask_human_cycle_blocked(mock_env, monkeypatch):
    # If the LLM loops: login (fail) -> ask_human -> login (fail) -> ask_human
    responses = [
        LLMResponse(thought="1", action="browser", args={"command": "login", "app": "Internal ERP"}, expected_outcome="Logged in"),
        LLMResponse(thought="2", action="ask_human", args={"question": "What is the password for Internal ERP?"}, expected_outcome="Received credentials"),
        LLMResponse(thought="3", action="browser", args={"command": "login", "app": "Internal ERP"}, expected_outcome="Logged in"),
        LLMResponse(thought="4", action="ask_human", args={"question": "What is the password for Internal ERP?"}, expected_outcome="Received credentials"),
        LLMResponse(thought="5", action="browser", args={"command": "login", "app": "Internal ERP"}, expected_outcome="Logged in"),
    ]
    llm = LoopMockLLM(responses)
    monkeypatch.setattr("agent.loop.generate_action", llm.generate_action)
    
    page = MockPage("http://erp")
    page._password_count = 1
    page._err_text = "Invalid"
    BrowserContext.page = page
    
    # We must patch Playwright so run_loop doesn't launch a real browser
    class MockPlaywright:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        class chromium:
            @staticmethod
            async def launch(*args, **kwargs):
                class Browser:
                    async def new_context(self):
                        class Context:
                            def set_default_timeout(self, t): pass
                            def set_default_navigation_timeout(self, t): pass
                            async def new_page(self): return page
                        return Context()
                    async def close(self): pass
                return Browser()
    monkeypatch.setattr("agent.loop.async_playwright", MockPlaywright)
    
    result = await run_loop("Test task", max_steps=10)
    
    # The loop should terminate with stalled/blocked before reaching step 5 due to no progress
    assert result["passed"] is False
    assert result["status"] in ["stalled", "blocked", "MAX_STEPS_REACHED"]
    assert llm.step < 6  # Loop terminated early
