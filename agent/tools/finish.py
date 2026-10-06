from .base import BaseTool, ToolResult, RiskLevel, registry
from pydantic import BaseModel, Field

class FinishArgs(BaseModel):
    summary: str = Field(description="Summary of task completion")

class FinishTool(BaseTool):
    name = "finish"
    description = "Call this when the task is complete to stop the loop."
    args_schema = FinishArgs
    risk_level = RiskLevel.READ

    async def run(self, summary: str) -> ToolResult:
        return ToolResult(ok=True, observation=summary)

registry.register(FinishTool())
