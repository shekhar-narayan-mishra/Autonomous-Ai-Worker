import asyncio
import time
import pytest
from agent.tools.base import BaseTool, ToolResult, RiskLevel
from agent.loop import execute_tool_with_timeout

class HangingAsyncTool(BaseTool):
    name = "hanging_async"
    description = "A tool that hangs asynchronously"
    risk_level = RiskLevel.READ

    async def run(self, **kwargs) -> ToolResult:
        await asyncio.sleep(10.0)
        return ToolResult(ok=True, observation="Done hanging")

class BlockingSyncTool(BaseTool):
    name = "blocking_sync"
    description = "A tool that blocks synchronously"
    risk_level = RiskLevel.READ

    def run(self, **kwargs) -> ToolResult:
        time.sleep(10.0)
        return ToolResult(ok=True, observation="Done blocking")

@pytest.mark.asyncio
async def test_hanging_async_tool_timeout():
    tool = HangingAsyncTool()
    start_t = time.time()
    res = await execute_tool_with_timeout(tool, {}, cap_seconds=2.0)
    elapsed = time.time() - start_t

    assert elapsed < 3.0, f"Hanging async tool took {elapsed:.2f}s, expected < 3.0s"
    assert res.ok is False
    assert "Timeout" in res.observation
    assert "hanging_async" in res.observation

@pytest.mark.asyncio
async def test_blocking_sync_tool_timeout():
    tool = BlockingSyncTool()
    start_t = time.time()
    res = await execute_tool_with_timeout(tool, {}, cap_seconds=2.0)
    elapsed = time.time() - start_t

    assert elapsed < 3.0, f"Blocking sync tool took {elapsed:.2f}s, expected < 3.0s"
    assert res.ok is False
    assert "Timeout" in res.observation
    assert "blocking_sync" in res.observation
