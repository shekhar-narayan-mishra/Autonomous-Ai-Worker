from pydantic import BaseModel, Field
from agent.tools.base import BaseTool, ToolResult, RiskLevel, registry

class MemoryStore:
    def __init__(self):
        self.facts: dict[str, dict] = {}
        self.history: list[dict] = []
        
    def save_fact(self, key: str, value: str | dict, source_step: int):
        self.facts[key] = {"value": value, "source_step": source_step}
        
    def format_for_prompt(self, rolling_window_size: int = 5) -> str:
        facts_str = "Known Facts:\n"
        for k, v in self.facts.items():
            facts_str += f"- {k}: {v['value']} (from step {v['source_step']})\n"
            
        if not self.history:
            return facts_str
            
        history_str = "\nRecent Steps:\n"
        recent = self.history[-rolling_window_size:]
        older = self.history[:-rolling_window_size]
        
        if older:
            history_str += "Older Summary:\n"
            for step in older:
                history_str += f"Step {step['step']}: {step['action']} -> {str(step['observation_summary'])[:50]}...\n"
                
        history_str += "\nDetailed Recent Steps:\n"
        for step in recent:
            history_str += f"Step {step['step']}: {step['action']}({step['args']}) -> {step['observation_summary']}\n"
            
        return facts_str + history_str

class SaveFactArgs(BaseModel):
    key: str = Field(description="Unique key for the fact")
    value: str = Field(description="Value to store")

class SaveFactTool(BaseTool):
    name = "save_fact"
    description = "Save an important fact to memory."
    args_schema = SaveFactArgs
    risk_level = RiskLevel.READ
    
    async def run(self, key: str, value: str) -> ToolResult:
        if hasattr(self, 'store'):
            self.store.save_fact(key, value, getattr(self, 'current_step', 0))
            return ToolResult(ok=True, observation=f"Fact '{key}' saved.")
        return ToolResult(ok=False, observation="Memory store not bound.")

class RecallArgs(BaseModel):
    key: str = Field(description="Key to recall")

class RecallTool(BaseTool):
    name = "recall"
    description = "Recall a fact from memory."
    args_schema = RecallArgs
    risk_level = RiskLevel.READ
    
    async def run(self, key: str) -> ToolResult:
        if hasattr(self, 'store') and key in self.store.facts:
            return ToolResult(ok=True, observation=str(self.store.facts[key]['value']))
        return ToolResult(ok=False, observation="Fact not found.")

save_fact_tool = SaveFactTool()
recall_tool = RecallTool()
registry.register(save_fact_tool)
registry.register(recall_tool)
