from unittest.mock import AsyncMock, patch

import pytest

from agent.safety import global_human_interface
from agent.tools.human import HumanTool


@pytest.mark.asyncio
async def test_human_tool_redaction():
    tool = HumanTool()
    
    with patch.object(global_human_interface, 'ask', new_callable=AsyncMock) as mock_ask:
        # 1. Ambiguity answer with 'key'
        mock_ask.return_value = "The primary key is 12345"
        res = await tool.run("Which key should I use for the database?")
        assert res.observation == "Human answered: The primary key is 12345"
        
        # 2. Password answer
        mock_ask.return_value = "mySecretPassword!"
        res = await tool.run("What is the password for the ERP?")
        assert res.observation == "Human answered: [REDACTED]"
        
        # 3. Login answer
        mock_ask.return_value = "admin"
        res = await tool.run("What is the login username?")
        assert res.observation == "Human answered: [REDACTED]"
        
        # 4. API Key answer
        mock_ask.return_value = "AIzaSyTestKey"
        res = await tool.run("Please provide the api key.")
        assert res.observation == "Human answered: [REDACTED]"
