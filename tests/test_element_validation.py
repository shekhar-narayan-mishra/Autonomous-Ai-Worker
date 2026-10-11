import pytest
from agent.tools.browser import BrowserTool, BrowserContext, validate_element_id

class MockPageForElements:
    def __init__(self):
        self.url = "http://example.com/form"
    async def evaluate(self, js):
        return {
            "url": self.url,
            "title": "Form Page",
            "elements": ["[e1] button \"Submit\"", "[e2] input type=text \"Name\""],
            "elements_info": {
                "e1": {"tag": "button", "display_tag": "button", "text": "Submit", "is_input": False},
                "e2": {"tag": "input", "display_tag": "input type=text", "text": "Name", "is_input": True}
            },
            "text": "Please enter your name",
            "is_json": False
        }

@pytest.mark.asyncio
async def test_element_id_validation_wrong_type():
    page = MockPageForElements()
    BrowserContext.page = page
    snap_str, is_dead, _ = await BrowserContext.snapshot()

    tool = BrowserTool()
    # e1 is a button, trying to type into it
    res = await tool.run(command="type", selector_id="e1", text="Hello")
    assert res.ok is False
    assert res.error == "wrong_approach"
    assert "Element e1 is <button>, not an input" in res.observation
    assert "Fillable fields here" in res.observation
    assert "e2" in res.observation

@pytest.mark.asyncio
async def test_element_id_validation_nonexistent():
    page = MockPageForElements()
    BrowserContext.page = page
    await BrowserContext.snapshot()

    tool = BrowserTool()
    # e99 does not exist
    res = await tool.run(command="click", selector_id="e99")
    assert res.ok is False
    assert res.error == "wrong_approach"
    assert "Element e99 not found in current snapshot" in res.observation
    assert "Available elements" in res.observation

@pytest.mark.asyncio
async def test_dead_end_detection_404():
    page = MockPageForElements()
    page.url = "http://example.com/api/dead"
    BrowserContext.page = page
    BrowserContext.last_good_url = "http://example.com/form"
    BrowserContext.last_http_status = 404

    snap_str, is_dead, _ = await BrowserContext.snapshot()
    assert is_dead is True

    tool = BrowserTool()
    res = await tool.run(command="snapshot")
    assert res.ok is False
    assert res.error == "dead_end"
    assert "Dead end http://example.com/api/dead (404)" in res.observation
    assert "Go back to http://example.com/form" in res.observation
