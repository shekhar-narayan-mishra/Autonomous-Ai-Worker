from pydantic import BaseModel, Field

from agent.safety import global_human_interface

from .base import BaseTool, RiskLevel, ToolResult, registry


class HumanArgs(BaseModel):
    question: str = Field(description="Question to ask the human or clarification needed.")

class HumanTool(BaseTool):
    name = "ask_human"
    description = "Ask the human for clarification when ambiguous or stuck. Do not guess."
    args_schema = HumanArgs
    risk_level = RiskLevel.READ
    
    async def run(self, question: str) -> ToolResult:
        ans = await global_human_interface.ask(question)
        if any(w in question.lower() for w in ["password", "login", "credential", "api key", "token to use"]):
            ans = "[REDACTED]"
        return ToolResult(ok=True, observation=f"Human answered: {ans}")

registry.register(HumanTool())
