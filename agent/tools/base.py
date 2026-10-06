from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Type, Any, Dict, List
from enum import Enum

class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

class ToolResult(BaseModel):
    ok: bool
    observation: str
    error: str | None = None
    error_type: str | None = None

class BaseTool(ABC):
    name: str
    description: str
    args_schema: Type[BaseModel]
    risk_level: RiskLevel

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        pass

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        return self._tools.get(name)

    def get_all(self) -> List[BaseTool]:
        return list(self._tools.values())

    def get_system_prompt_segment(self) -> str:
        lines = []
        for tool in self.get_all():
            schema = tool.args_schema.model_json_schema()
            lines.append(f"- {tool.name}: {tool.description} | Args: {schema.get('properties', {})} | Risk: {tool.risk_level.value}")
        return "\n".join(lines)

registry = ToolRegistry()
