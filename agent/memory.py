from pydantic import BaseModel, Field

from agent.tools.base import BaseTool, RiskLevel, ToolResult, registry


class MemoryStore:
    def __init__(self):
        self.facts: dict[str, dict] = {}
        self.history: list[dict] = []
        
    def save_fact(self, key: str, value: str | dict, source_step: int):
        self.facts[key] = {"value": value, "source_step": source_step}
        
    def format_for_prompt(self, rolling_window_size: int = 4) -> str:
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
    key: str | None = Field(None, description="Unique key for a single fact")
    value: str | None = Field(None, description="Value to store for a single fact")
    facts: dict[str, str] | None = Field(None, description="Object containing multiple key-value pairs to store at once")

class SaveFactTool(BaseTool):
    name = "save_fact"
    description = "Save an important fact to memory."
    args_schema = SaveFactArgs
    risk_level = RiskLevel.READ
    
    def __init__(self):
        super().__init__()
        self.last_keys = set()
        
    async def run(self, key: str = None, value: str = None, facts: dict = None) -> ToolResult:
        if not hasattr(self, 'store'):
            return ToolResult(ok=False, observation="Memory store not bound.")
            
        new_facts = facts or {}
        if key and value is not None:
            new_facts[key] = value
            
        if not new_facts:
            return ToolResult(ok=False, observation="Must provide key/value or facts object.")
            
        keys_set = set(new_facts.keys())
        if self.last_keys == keys_set:
            return ToolResult(ok=False, observation=f"Error: You just called save_fact with keys {list(keys_set)}. You may not call it with the exact same keys twice in a row.")
            
        self.last_keys = keys_set
        for k, v in new_facts.items():
            self.store.save_fact(k, v, getattr(self, 'current_step', 0))
            
        saved_keys = ", ".join(new_facts.keys())
        return ToolResult(ok=True, observation=f"Facts saved: {saved_keys}")

class RecallArgs(BaseModel):
    key: str = Field(description="Key to recall")

class RecallTool(BaseTool):
    name = "recall"
    description = "Recall a fact from memory."
    args_schema = RecallArgs
    risk_level = RiskLevel.READ

    def __init__(self):
        super().__init__()
        self.last_key = None
        
    async def run(self, key: str) -> ToolResult:
        if self.last_key == key:
            return ToolResult(ok=False, observation=f"Error: You just called recall with key '{key}'. You may not call it with the same key twice in a row.")
        self.last_key = key
        
        if hasattr(self, 'store') and key in self.store.facts:
            return ToolResult(ok=True, observation=str(self.store.facts[key]['value']))
        return ToolResult(ok=True, observation="not found")

save_fact_tool = SaveFactTool()
recall_tool = RecallTool()
registry.register(save_fact_tool)
registry.register(recall_tool)
