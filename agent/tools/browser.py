import asyncio
from playwright.async_api import Page
from .base import BaseTool, ToolResult, RiskLevel, registry
from pydantic import BaseModel, Field

class BrowserContext:
    page: Page | None = None
    elements_map: dict[str, str] = {}

    @classmethod
    async def snapshot(cls) -> str:
        if not cls.page:
            return "Browser not initialized."
        
        js = """
        () => {
            let nextId = 1;
            const elements = document.querySelectorAll('button, a, input, select, textarea, [role="button"]');
            const result = [];
            for (const el of elements) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) continue;
                if (!el.hasAttribute('data-aid')) {
                    el.setAttribute('data-aid', 'e' + nextId++);
                }
                const aid = el.getAttribute('data-aid');
                let tag = el.tagName.toLowerCase();
                let text = el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '';
                if (tag === 'input') {
                    tag = 'input type=' + el.type;
                }
                text = text.trim().substring(0, 50).replace(/\\n/g, ' ');
                result.push(`[${aid}] ${tag} "${text}"`);
            }
            let bodyText = document.body.innerText.substring(0, 500).replace(/\\n/g, ' ');
            return {
                url: document.location.href,
                title: document.title,
                elements: result,
                text: bodyText
            };
        }
        """
        data = await cls.page.evaluate(js)
        cls.elements_map = {item.split(' ')[0][1:-1]: f"[data-aid='{item.split(' ')[0][1:-1]}']" for item in data['elements']}
        
        elements_str = "\n".join(data['elements'])
        return f"URL: {data['url']}\nTitle: {data['title']}\nVisible Text: {data['text']}...\nInteractive Elements:\n{elements_str}"

class BrowserArgs(BaseModel):
    command: str = Field(description="goto, click, type, select, press, wait, snapshot, extract_text")
    url: str | None = Field(None, description="URL for goto")
    selector_id: str | None = Field(None, description="The e12 ID of the element to interact with")
    text: str | None = Field(None, description="Text to type or select")
    key: str | None = Field(None, description="Key to press (e.g. Enter)")

class BrowserTool(BaseTool):
    name = "browser"
    description = "Control the web browser. Commands: goto, click, type, select, press, wait, snapshot, extract_text."
    args_schema = BrowserArgs
    risk_level = RiskLevel.READ

    async def run(self, command: str, url: str = None, selector_id: str = None, text: str = None, key: str = None) -> ToolResult:
        page = BrowserContext.page
        if not page and command != 'goto':
            return ToolResult(ok=False, observation="Browser not initialized. Use goto first.")
        
        try:
            if command == "goto":
                await page.goto(url)
                await page.wait_for_load_state("networkidle")
            elif command == "click":
                selector = f"[data-aid='{selector_id}']"
                await page.click(selector, timeout=5000)
                await page.wait_for_load_state("networkidle")
            elif command == "type":
                selector = f"[data-aid='{selector_id}']"
                await page.fill(selector, text, timeout=5000)
            elif command == "select":
                selector = f"[data-aid='{selector_id}']"
                await page.select_option(selector, label=text, timeout=5000)
            elif command == "press":
                await page.keyboard.press(key)
                await page.wait_for_load_state("networkidle")
            elif command == "wait":
                await asyncio.sleep(2)
            elif command == "snapshot":
                pass
            elif command == "extract_text":
                return ToolResult(ok=True, observation=(await page.evaluate("document.body.innerText"))[:2000])
            else:
                return ToolResult(ok=False, observation=f"Unknown command: {command}")
            
            snap = await BrowserContext.snapshot()
            return ToolResult(ok=True, observation=snap)
        except Exception as e:
            return ToolResult(ok=False, observation=str(e), error=str(e), error_type=type(e).__name__)

registry.register(BrowserTool())
