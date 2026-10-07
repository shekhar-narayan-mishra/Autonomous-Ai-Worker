from pydantic import BaseModel

from agent.llm import build_action_args_model
from agent.tools.base import BaseTool, RiskLevel, ToolRegistry, ToolResult, registry


def test_tool_schema_conformance():
    """Ensure that EVERY real registered tool's arguments can be expressed by the dynamically generated ActionArgs."""
    ActionArgs = build_action_args_model(registry)
    supported_fields = set(ActionArgs.model_fields.keys())
    
    missing_fields = []
    
    for tool in registry.get_all():
        tool_name = tool.name
        if not tool.args_schema:
            continue
            
        for field_name in tool.args_schema.model_fields.keys():
            if field_name not in supported_fields:
                missing_fields.append(f"Tool '{tool_name}' requires field '{field_name}' not in ActionArgs")
    
    assert not missing_fields, "Some real tool args cannot be expressed in ActionArgs:\n" + "\n".join(missing_fields)

def test_fake_tool_schema_conformance():
    """Ensure that a fake tool with a custom schema correctly influences the generated ActionArgs."""
    test_registry = ToolRegistry()
    
    class FakeToolArgs(BaseModel):
        fake_field: str
        another_field: int
        
    class FakeTool(BaseTool):
        name = "fake_tool"
        description = "A fake tool"
        args_schema = FakeToolArgs
        risk_level = RiskLevel.READ
        
        async def run(self, **kwargs) -> ToolResult:
            return ToolResult(ok=True, observation="Fake")
            
    test_registry.register(FakeTool())
    
    ActionArgs = build_action_args_model(test_registry)
    supported_fields = set(ActionArgs.model_fields.keys())
    
    assert "fake_field" in supported_fields
    assert "another_field" in supported_fields
    
    payload = {"fake_field": "test", "another_field": 42}
    parsed = ActionArgs(**payload)
    assert parsed.fake_field == "test"
    assert parsed.another_field == 42
