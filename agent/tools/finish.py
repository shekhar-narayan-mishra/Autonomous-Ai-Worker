from pydantic import BaseModel, Field

from .base import BaseTool, RiskLevel, ToolResult, registry


class FinishArgs(BaseModel):
    summary: str = Field(description="Summary of task completion")
    evidence: str = Field(description="Citations (URL/step) from the source system where data was observed")

class FinishTool(BaseTool):
    name = "finish"
    description = "Call this when the task is complete to stop the loop. Must include evidence."
    args_schema = FinishArgs
    risk_level = RiskLevel.READ

    async def run(self, summary: str, evidence: str = "") -> ToolResult:
        if not evidence.strip():
            return ToolResult(ok=False, observation="Error: finish requires an 'evidence' field citing observations (e.g., URL/step).")
        return ToolResult(ok=True, observation=f"Summary: {summary}\nEvidence: {evidence}")

registry.register(FinishTool())
