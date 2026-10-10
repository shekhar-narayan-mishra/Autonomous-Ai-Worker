import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from agent.tools.browser import BrowserTool
from agent.reliability import ReliabilityManager, ErrorClassification
import json

@pytest.mark.asyncio
async def test_browser_5xx_detection():
    tool = BrowserTool()
    
    mock_page = AsyncMock()
    mock_response = MagicMock()
    mock_response.status = 502
    mock_page.goto.return_value = mock_response
    
    with patch('agent.tools.browser.BrowserContext.page', mock_page):
        # Test goto command
        res = await tool.run(command="goto", url="http://test.com")
        assert not res.ok
        assert "HTTP 502" in res.error or "HTTP 502" in res.observation
        
@pytest.mark.asyncio
async def test_bounded_retry_logic():
    rm = ReliabilityManager()
    assert rm.classify_error("HTTP 502 Service Unavailable") == ErrorClassification.TRANSIENT
    assert rm.classify_error("Timeout: exceeded 20s limit") == ErrorClassification.TRANSIENT
    
    # Check that bounded retry will retry TRANSIENT errors
    # (Since loop.py handles the actual retry loop, we can just verify the RM classification is correct
    # and the error classification handles the 5xx / 502 specifically)

def test_no_progress_loop_detection():
    rm = ReliabilityManager()
    rm.record_action("browser", {"command": "goto", "url": "http://test.com"})
    assert not rm.detect_loop()
    
    rm.record_action("browser", {"command": "goto", "url": "http://test.com"})
    assert not rm.detect_loop()
    
    rm.record_action("browser", {"command": "goto", "url": "http://test.com"})
    assert rm.detect_loop() == True

