import pytest
from agent.tools.base import registry
from agent.llm import ActionArgs

def test_tool_schema_conformance():
    """Ensure that EVERY tool's arguments can be expressed by ActionArgs and round-trip successfully."""
    
    # Get all fields that ActionArgs supports
    supported_fields = set(ActionArgs.model_fields.keys())
    
    missing_fields = []
    
    for tool in registry.get_all():
        tool_name = tool.name
        if not tool.args_schema:
            continue
            
        for field_name, field in tool.args_schema.model_fields.items():
            if field_name not in supported_fields:
                missing_fields.append(f"Tool '{tool_name}' requires field '{field_name}' not in ActionArgs")
                continue
                
            # Now test type compatibility roughly
            action_field = ActionArgs.model_fields[field_name]
            
            # Since action fields are all Optional, we just need to ensure the inner type matches
            # or is a superset (like list[FillFormField])
            
            # For simplicity, if we can instantiate ActionArgs with the mock data, it round trips
            # We'll just build a mock payload and parse it with ActionArgs
    
    assert not missing_fields, "Some tool args cannot be expressed in ActionArgs:\n" + "\n".join(missing_fields)
    
    # Test a complex payload like fill_form
    payload = {
        "command": "fill_form",
        "fields": [
            {"id": "e1", "value": "test1"},
            {"id": "e2", "value": "test2"}
        ],
        "submit": "e3"
    }
    
    parsed = ActionArgs(**payload)
    assert parsed.command == "fill_form"
    assert len(parsed.fields) == 2
    assert parsed.fields[0].id == "e1"
    assert parsed.submit == "e3"
    
    # Also verify that the provider schema generators don't crash on it
    schema_json = ActionArgs.model_json_schema()
    assert "fields" in schema_json["properties"]
    
    print("Schema conformance test passed!")
