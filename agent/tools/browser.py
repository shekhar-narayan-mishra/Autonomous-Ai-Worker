import asyncio
from urllib.parse import urlparse

from playwright.async_api import Page
from pydantic import BaseModel, Field

import config
from .base import BaseTool, RiskLevel, ToolResult, registry


def get_origin(u: str) -> str:
    if not u:
        return ""
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "http://" + u
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


class SnapshotResult(str):
    def __new__(cls, content: str, is_dead_end: bool = False, is_json: bool = False):
        obj = super().__new__(cls, content)
        obj.is_dead_end = is_dead_end
        obj.is_json = is_json
        return obj

    def __iter__(self):
        return iter((str(self), self.is_dead_end, self.is_json))


class BrowserContext:
    page: Page | None = None
    elements_map: dict[str, str] = {}
    elements_info: dict[str, dict] = {}
    last_http_status: int = 200
    last_good_url: str | None = None
    navigated_origins: set[str] = set()

    @classmethod
    def reset(cls):
        cls.elements_map = {}
        cls.elements_info = {}
        cls.last_http_status = 200
        cls.last_good_url = None
        cls.navigated_origins = set()

    @classmethod
    async def snapshot(cls) -> SnapshotResult:
        """
        Returns SnapshotResult (usable as str or unpacked as snapshot_str, is_dead_end, is_json)
        """
        if not cls.page:
            return SnapshotResult("Browser not initialized.", False, False)

        js = """
        () => {
            let nextId = 1;
            const elements = document.querySelectorAll('button, a, input, select, textarea, [role="button"]');
            const result = [];
            const infoMap = {};
            for (const el of elements) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) continue;
                if (!el.hasAttribute('data-aid')) {
                    el.setAttribute('data-aid', 'e' + nextId++);
                }
                const aid = el.getAttribute('data-aid');
                let rawTag = el.tagName.toLowerCase();
                let displayTag = rawTag;
                if (rawTag === 'input') {
                    displayTag = 'input type=' + (el.type || 'text');
                }
                let text = el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '';
                text = text.trim().substring(0, 50).replace(/\\n/g, ' ');
                result.push(`[${aid}] ${displayTag} "${text}"`);
                infoMap[aid] = {
                    tag: rawTag,
                    display_tag: displayTag,
                    text: text,
                    is_input: (rawTag === 'input' || rawTag === 'textarea' || rawTag === 'select')
                };
            }
            let bodyText = document.body.innerText.substring(0, 300).replace(/\\n/g, ' ');
            const isJson = (document.contentType === 'application/json') || 
                           (bodyText.trim().startsWith('{') && bodyText.trim().endsWith('}')) ||
                           (document.body.children.length === 1 && document.body.children[0].tagName.toLowerCase() === 'pre' && bodyText.trim().startsWith('{'));
            return {
                url: document.location.href,
                title: document.title,
                elements: result.slice(0, 25),
                elements_info: infoMap,
                text: bodyText,
                is_json: isJson
            };
        }
        """
        try:
            data = await cls.page.evaluate(js)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

        cls.elements_info = data.get("elements_info") if isinstance(data.get("elements_info"), dict) else {}
        if not cls.elements_info and "elements" in data and isinstance(data["elements"], list):
            for item in data.get("elements", []):
                aid = item.split(' ')[0][1:-1]
                cls.elements_info[aid] = {
                    "tag": "button" if "button" in item else "input",
                    "display_tag": item.split(' ')[1] if len(item.split(' ')) > 1 else "element",
                    "text": item,
                    "is_input": "input" in item or "textarea" in item or "select" in item
                }
        cls.elements_map = {aid: f"[data-aid='{aid}']" for aid in cls.elements_info}

        current_url = getattr(cls.page, "url", "") if isinstance(getattr(cls.page, "url", None), str) else ""
        if isinstance(data.get("url"), str) and data["url"]:
            current_url = data["url"]
        elements_list = data.get("elements", []) if isinstance(data.get("elements"), list) else []
        elements_str = "\n".join(elements_list)
        raw_snap = f"HTTP Status: {cls.last_http_status}\nURL: {current_url}\nTitle: {data.get('title', '')}\nVisible Text: {data.get('text', '')}...\nInteractive Elements:\n{elements_str}"
        if len(raw_snap) > 3000:
            raw_snap = raw_snap[:3000] + "\n[Snapshot truncated]"

        is_dead_end = (cls.last_http_status >= 400) or data.get("is_json", False)
        if not is_dead_end and cls.last_http_status < 400:
            cls.last_good_url = current_url

        return SnapshotResult(raw_snap, is_dead_end, data.get("is_json", False))


def validate_element_id(selector_id: str, for_input: bool = False) -> tuple[bool, str]:
    if not selector_id:
        return False, "Missing selector_id."
    if selector_id not in BrowserContext.elements_info:
        avail = [f"[{aid}] {info.get('display_tag', '')} \"{info.get('text', '')}\"" for aid, info in BrowserContext.elements_info.items()]
        avail_str = ", ".join(avail[:8]) if avail else "none"
        return False, f"Element {selector_id} not found in current snapshot. Available elements: [{avail_str}]"

    info = BrowserContext.elements_info[selector_id]
    if for_input and not info.get("is_input", False):
        fillable = [f"[{aid}] {inf.get('display_tag', '')} \"{inf.get('text', '')}\"" for aid, inf in BrowserContext.elements_info.items() if inf.get("is_input", False)]
        fillable_str = ", ".join(fillable) if fillable else "none"
        return False, f"Element {selector_id} is <{info.get('tag', 'unknown')}>, not an input. Fillable fields here: [{fillable_str}]"

    return True, ""


async def navigate_with_retry(page: Page, url: str) -> tuple[bool, str, int]:
    """
    Auto-retry (2 retries, backoff) for navigation timeouts and 5xx BEFORE returning anything to the model.
    First navigation per app/origin uses a 60s timeout; later actions 10s.
    Returns (success, error_msg, http_status).
    """
    origin = get_origin(url)
    is_first = origin not in BrowserContext.navigated_origins
    timeout_ms = 60000 if is_first else 10000

    last_err = ""
    status = 200

    for attempt in range(3):  # 1 initial + 2 retries
        try:
            try:
                resp = await page.goto(url, timeout=timeout_ms)
            except TypeError:
                resp = await page.goto(url)
            status = resp.status if (resp and hasattr(resp, "status")) else 200
            BrowserContext.last_http_status = status
            if status >= 500:
                raise Exception(f"HTTP {status} Server Error on navigation to {url}")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except TypeError:
                await page.wait_for_load_state("domcontentloaded")
            BrowserContext.navigated_origins.add(origin)
            if status < 400:
                BrowserContext.last_good_url = page.url
            return True, "", status
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                backoff = 1.0 * (2 ** attempt)
                await asyncio.sleep(backoff)
            else:
                BrowserContext.last_http_status = 500 if status == 200 else status
                return False, last_err, BrowserContext.last_http_status
    return False, last_err, BrowserContext.last_http_status



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
    description = "Control the web browser. Commands: goto, click, type, select, press, wait, snapshot, extract_text, fill_form, login."
    args_schema = BrowserArgs
    risk_level = RiskLevel.READ

    async def run(self, command: str, url: str = None, selector_id: str = None, text: str = None, key: str = None, fields: list[dict] = None, submit: str = None, app: str = None) -> ToolResult:
        page = BrowserContext.page
        if not page and command != 'goto':
            return ToolResult(ok=False, observation="Browser not initialized. Use goto first.")

        try:
            feedback = ""
            if command == "goto":
                ok, err_msg, status = await navigate_with_retry(page, url)
                if not ok:
                    snap_str, _, _ = await BrowserContext.snapshot()
                    return ToolResult(
                        ok=False,
                        observation=f"Navigation to {url} failed: {err_msg} (HTTP {status}).\n{snap_str}",
                        error=err_msg
                    )

            elif command == "click":
                # Validate element against current snapshot
                valid, err_msg = validate_element_id(selector_id, for_input=False)
                if not valid:
                    return ToolResult(ok=False, observation=err_msg, error="wrong_approach")

                selector = f"[data-aid='{selector_id}']"
                try:
                    await page.click(selector, timeout=10000)
                except TypeError:
                    await page.click(selector)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except TypeError:
                    await page.wait_for_load_state("domcontentloaded")

            elif command == "type":
                # Validate element against current snapshot (must be input)
                valid, err_msg = validate_element_id(selector_id, for_input=True)
                if not valid:
                    return ToolResult(ok=False, observation=err_msg, error="wrong_approach")

                selector = f"[data-aid='{selector_id}']"
                try:
                    await page.fill(selector, text or "", timeout=10000)
                except TypeError:
                    await page.fill(selector, text or "")
                feedback = f" ({selector_id} now '{text}')"

            elif command == "select":
                # Validate element against current snapshot
                valid, err_msg = validate_element_id(selector_id, for_input=False)
                if not valid:
                    return ToolResult(ok=False, observation=err_msg, error="wrong_approach")

                selector = f"[data-aid='{selector_id}']"
                try:
                    await page.select_option(selector, label=text, timeout=10000)
                except TypeError:
                    await page.select_option(selector, label=text)

            elif command == "press":
                await page.keyboard.press(key)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except TypeError:
                    await page.wait_for_load_state("domcontentloaded")

            elif command == "wait":
                await asyncio.sleep(2)

            elif command == "fill_form":
                if fields:
                    # Validate all fields before typing into any
                    for field in fields:
                        sid = field.id if hasattr(field, 'id') else field.get('id')
                        valid, err_msg = validate_element_id(sid, for_input=True)
                        if not valid:
                            return ToolResult(ok=False, observation=err_msg, error="wrong_approach")

                    if submit:
                        valid, err_msg = validate_element_id(submit, for_input=False)
                        if not valid:
                            return ToolResult(ok=False, observation=err_msg, error="wrong_approach")

                    feedback_parts = []
                    for field in fields:
                        sid = field.id if hasattr(field, 'id') else field.get('id')
                        val = field.value if hasattr(field, 'value') else field.get('value', '')
                        await page.fill(f"[data-aid='{sid}']", str(val), timeout=10000)
                        feedback_parts.append(f"{sid} now '{val}'")
                    feedback = " (" + ", ".join(feedback_parts) + ")"

                if submit:
                    await page.click(f"[data-aid='{submit}']", timeout=10000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)

            elif command == "snapshot":
                pass

            elif command == "extract_text":
                return ToolResult(ok=True, observation=(await page.evaluate("document.body.innerText"))[:2000])

            elif command == "login":
                env_dict = config.load_environment()

                def normalize(s):
                    if not s:
                        return ""
                    return s.lower().replace(" ", "").replace("_", "").replace("-", "")

                valid_apps = [a.get("name") for a in env_dict.get("apps", []) if a.get("name")]
                valid_apps_str = ", ".join(f'"{name}"' for name in valid_apps)

                target_app_norm = normalize(app)
                target_origin = get_origin(app) if app else None
                current_origin = get_origin(page.url) if page.url else None

                target_app_config = None
                for a in env_dict.get("apps", []):
                    if target_app_norm and normalize(a.get("name")) == target_app_norm:
                        target_app_config = a
                        break

                if not target_app_config and target_origin:
                    for a in env_dict.get("apps", []):
                        if get_origin(a.get("base_url")) == target_origin:
                            target_app_config = a
                            break

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

                if base_url:
                    t_orig = get_origin(base_url)
                    c_orig = get_origin(page.url) if page.url else ""
                    if c_orig != t_orig or page.url == "about:blank":
                        ok, err_msg, status = await navigate_with_retry(page, base_url)
                        if not ok:
                            snap_str, _, _ = await BrowserContext.snapshot()
                            return ToolResult(ok=False, observation=f"Navigation to {base_url} failed: {err_msg} (HTTP {status}).\n{snap_str}", error=err_msg)

                # Check if already authenticated
                password_inputs = await page.locator("input[type='password']").count()
                if password_inputs == 0:
                    snap, is_dead, _ = await BrowserContext.snapshot()
                    return ToolResult(ok=True, observation=f"Appears already authenticated or no login form found. Current URL: {page.url}\nPage state:\n{snap}")

                await page.locator("input[type='text'], input[type='email'], input:not([type])").first.fill(creds.get("username", ""))
                await page.locator("input[type='password']").first.fill(creds.get("password", ""))
                try:
                    await page.locator("button[type='submit'], input[type='submit'], button").first.click(timeout=10000)
                except TypeError:
                    await page.locator("button[type='submit'], input[type='submit'], button").first.click()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except TypeError:
                    await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                err_text = await page.evaluate("""() => {
                    const el = document.querySelector('.error, .alert, [role="alert"], [data-error]');
                    return el ? el.innerText : '';
                }""")

                snap, is_dead, _ = await BrowserContext.snapshot()
                if err_text:
                    return ToolResult(ok=False, observation=f"Login failed for {app}: {err_text}. URL: {page.url}\nPage state:\n{snap}")
                return ToolResult(ok=True, observation=f"Logged in successfully. Resulting URL: {page.url}\nPage state:\n{snap}")

            else:
                return ToolResult(ok=False, observation=f"Unknown command: {command}")

            snap, is_dead, is_json = await BrowserContext.snapshot()

            # Dead end check (HTTP status >= 400 or JSON-only page)
            if is_dead:
                last_url = BrowserContext.last_good_url or "the home page"
                return ToolResult(
                    ok=False,
                    observation=f"Dead end {page.url} ({BrowserContext.last_http_status}). Go back to {last_url} or use a link from the last snapshot.\nPage state:\n{snap}",
                    error="dead_end"
                )

            return ToolResult(ok=True, observation=snap + feedback)

        except Exception as e:
            err_obs = str(e)
            try:
                if BrowserContext.page:
                    snap, _, _ = await BrowserContext.snapshot()
                    err_obs += f"\nPage state after error:\n{snap}"
            except Exception:
                pass
            return ToolResult(ok=False, observation=err_obs, error=str(e), error_type=type(e).__name__)


registry.register(BrowserTool())
