from .base import BaseTool, ToolResult, RiskLevel, registry
from pydantic import BaseModel, Field

class FileArgs(BaseModel):
    command: str = Field(description="read_text, read_csv")
    path: str = Field(description="File path")

class FilesTool(BaseTool):
    name = "files"
    description = "Read local files"
    args_schema = FileArgs
    risk_level = RiskLevel.READ

    async def run(self, command: str, path: str) -> ToolResult:
        try:
            with open(path, "r") as f:
                content = f.read()[:2000]
            return ToolResult(ok=True, observation=content)
        except Exception as e:
            return ToolResult(ok=False, observation=str(e), error=str(e), error_type=type(e).__name__)

registry.register(FilesTool())
