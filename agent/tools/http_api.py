import httpx
from .base import BaseTool, ToolResult, RiskLevel, registry
from pydantic import BaseModel, Field

class HttpArgs(BaseModel):
    method: str = Field(description="GET or POST")
    url: str
    body: dict | None = None

class HttpTool(BaseTool):
    name = "http_api"
    description = "Make HTTP requests"
    args_schema = HttpArgs
    risk_level = RiskLevel.WRITE

    async def run(self, method: str, url: str, body: dict = None) -> ToolResult:
        try:
            async with httpx.AsyncClient() as client:
                if method.upper() == "GET":
                    r = await client.get(url)
                else:
                    r = await client.post(url, json=body)
                return ToolResult(ok=True, observation=f"Status: {r.status_code}\nBody: {r.text[:1000]}")
        except Exception as e:
            return ToolResult(ok=False, observation=str(e), error=str(e), error_type=type(e).__name__)

registry.register(HttpTool())
