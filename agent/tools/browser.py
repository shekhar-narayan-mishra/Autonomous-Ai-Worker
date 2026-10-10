import asyncio

from playwright.async_api import Page
from pydantic import BaseModel, Field

from .base import BaseTool, RiskLevel, ToolResult, registry


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
            let bodyText = document.body.innerText.substring(0, 300).replace(/\\n/g, ' ');
            return {
                url: document.location.href,
                title: document.title,
                elements: result.slice(0, 25),
                text: bodyText
            };
        }
        """
        data = await cls.page.evaluate(js)
        cls.elements_map = {item.split(' ')[0][1:-1]: f"[data-aid='{item.split(' ')[0][1:-1]}']" for item in data['elements']}
        
        elements_str = "\n".join(data['elements'])
        raw_snap = f"URL: {data['url']}\nTitle: {data['title']}\nVisible Text: {data['text']}...\nInteractive Elements:\n{elements_str}"
        # Max ~800 tokens (~3000 chars)
        if len(raw_snap) > 3000:
            raw_snap = raw_snap[:3000] + "\n[Snapshot truncated]"
        return raw_snap

class FillFormField(BaseModel):
    id: str = Field(description="The e12 ID of the field to type into")
    value: str = Field(description="The text to type")

class BrowserArgs(BaseModel):
    command: str = Field(description="goto, click, type, select, press, wait, snapshot, extract_text, fill_form, login")
    url: str | None = Field(None, description="URL for goto")
    app: str | None = Field(None, description="App name for login")
    selector_id: str | None = Field(None, description="The e12 ID of the element to interact with")
    text: str | None = Field(None, description="Text to type or select")
    key: str | None = Field(None, description="Key to press (e.g. Enter)")
    fields: list[FillFormField] | None = Field(None, description="For fill_form: List of fields to type into")
    submit: str | None = Field(None, description="For fill_form: e12 ID of the submit button to click")

class BrowserTool(BaseTool):
    name = "browser"
    description = "Control the web browser. Commands: goto, click, type, select, press, wait, snapshot, extract_text, fill_form."
    args_schema = BrowserArgs
    risk_level = RiskLevel.READ

    async def run(self, command: str, url: str = None, selector_id: str = None, text: str = None, key: str = None, fields: list[dict] = None, submit: str = None, app: str = None) -> ToolResult:
        page = BrowserContext.page
        if not page and command != 'goto':
            return ToolResult(ok=False, observation="Browser not initialized. Use goto first.")
        
        try:
            feedback = ""
            if command == "goto":
                resp = await page.goto(url)
                if resp and resp.status >= 500:
                    raise Exception(f"HTTP {resp.status} Service Unavailable on navigation to {url}.")
                await page.wait_for_load_state("domcontentloaded")
            elif command == "click":
                selector = f"[data-aid='{selector_id}']"
                await page.click(selector, timeout=5000)
                await page.wait_for_load_state("domcontentloaded")
            elif command == "type":
                selector = f"[data-aid='{selector_id}']"
                await page.fill(selector, text, timeout=5000)
                feedback = f" ({selector_id} now '{text}')"
            elif command == "select":
                selector = f"[data-aid='{selector_id}']"
                await page.select_option(selector, label=text, timeout=5000)
            elif command == "press":
                await page.keyboard.press(key)
                await page.wait_for_load_state("domcontentloaded")
            elif command == "wait":
                await asyncio.sleep(2)
            elif command == "fill_form":
                if fields:
                    feedback_parts = []
                    for field in fields:
                        # Depending on pydantic version / whether dict or object is passed
                        sid = field.id if hasattr(field, 'id') else field['id']
                        val = field.value if hasattr(field, 'value') else field['value']
                        await page.fill(f"[data-aid='{sid}']", val, timeout=5000)
                        feedback_parts.append(f"{sid} now '{val}'")
                    feedback = " (" + ", ".join(feedback_parts) + ")"
                if submit:
                    await page.click(f"[data-aid='{submit}']", timeout=5000)
                    await page.wait_for_load_state("domcontentloaded")
            elif command == "snapshot":
                pass
            elif command == "extract_text":
                return ToolResult(ok=True, observation=(await page.evaluate("document.body.innerText"))[:2000])
            elif command == "login":
                from urllib.parse import urlparse

                import yaml
                with open("config/environment.yaml") as f:
                    env_dict = yaml.safe_load(f)
                
                creds = None
                
                def normalize(s):
                    if not s: return ""
                    return s.lower().replace(" ", "").replace("_", "").replace("-", "")
                
                def get_origin(u):
                    if not u: return ""
                    if not u.startswith("http"): u = "http://" + u
                    try:
                        p = urlparse(u)
                        return f"{p.scheme}://{p.netloc}"
                    except:
                        return ""
                
                valid_apps = [a.get("name") for a in env_dict.get("apps", []) if a.get("name")]
                valid_apps_str = ", ".join(f'"{name}"' for name in valid_apps)
                
                target_app_norm = normalize(app)
                target_origin = get_origin(app) if app else None
                current_origin = get_origin(page.url) if page.url else None
                
                # a) normalized name
                target_app_config = None
                for a in env_dict.get("apps", []):
                    if target_app_norm and normalize(a.get("name")) == target_app_norm:
                        target_app_config = a
                        break
                
                # b) URL or host:port string
                if not target_app_config and target_origin:
                    for a in env_dict.get("apps", []):
                        if get_origin(a.get("base_url")) == target_origin:
                            target_app_config = a
                            break
                            
                # c) if omitted or unmatched, current page origin
                if not target_app_config and current_origin:
                    for a in env_dict.get("apps", []):
                        if get_origin(a.get("base_url")) == current_origin:
                            target_app_config = a
                            break

                if not target_app_config:
                    app_display = app if app else "None"
                    return ToolResult(ok=False, observation=f'Unknown app "{app_display}". Valid apps: {valid_apps_str}. Use browser login with one of these names.')
                
                creds = target_app_config.get("credentials", {})
                base_url = target_app_config.get("base_url")

                # target-aware navigation
                if base_url:
                    t_orig = get_origin(base_url)
                    c_orig = get_origin(page.url) if page.url else ""
                    if c_orig != t_orig or page.url == "about:blank":
                        resp = await page.goto(base_url)
                        if resp and resp.status >= 500:
                            raise Exception(f"HTTP {resp.status} Service Unavailable on navigation to {base_url}.")
                        await page.wait_for_load_state("domcontentloaded")

                # state-aware checks
                password_inputs = await page.locator("input[type='password']").count()
                if password_inputs == 0:
                    snap = await BrowserContext.snapshot()
                    return ToolResult(ok=True, observation=f"Appears already authenticated or no login form found. Current URL: {page.url}\nPage state:\n{snap}")

                await page.locator("input[type='text'], input[type='email'], input:not([type])").first.fill(creds.get("username", ""))
                await page.locator("input[type='password']").first.fill(creds.get("password", ""))
                await page.locator("button[type='submit'], input[type='submit'], button").first.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                
                err_text = await page.evaluate("""() => {
                    const el = document.querySelector('.error, .alert, [role="alert"], [data-error]');
                    return el ? el.innerText : '';
                }""")
                
                snap = await BrowserContext.snapshot()
                if err_text:
                    return ToolResult(ok=False, observation=f"Login failed for {app}: {err_text}. URL: {page.url}\nPage state:\n{snap}")
                return ToolResult(ok=True, observation=f"Logged in successfully. Resulting URL: {page.url}\nPage state:\n{snap}")
            else:
                return ToolResult(ok=False, observation=f"Unknown command: {command}")
            
            snap = await BrowserContext.snapshot()
            return ToolResult(ok=True, observation=snap + feedback)
        except Exception as e:
            err_obs = str(e)
            try:
                if BrowserContext.page:
                    snap = await BrowserContext.snapshot()
                    err_obs += f"\nPage state after error:\n{snap}"
            except Exception:
                pass
            return ToolResult(ok=False, observation=err_obs, error=str(e), error_type=type(e).__name__)

registry.register(BrowserTool())
