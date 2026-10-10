import asyncio
import os
from pydantic import BaseModel, Field

os.environ["GEMINI_API_KEY"] = "fake"

async def test_schema():
    from google import genai
    from google.genai import types
    
    class TestSchema(BaseModel):
        field1: str
        
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "fake"))
    
    schema_dict = TestSchema.model_json_schema()
    def remove_additional_properties(d):
        if isinstance(d, dict):
            if "additionalProperties" in d:
                del d["additionalProperties"]
            for k, v in d.items():
                remove_additional_properties(v)
        elif isinstance(d, list):
            for item in d:
                remove_additional_properties(item)
        return d
    schema_dict = remove_additional_properties(schema_dict)
    
    try:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_dict,
        )
        print("Success!", config.response_schema)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test_schema())
