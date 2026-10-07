import os
import re
import time
import json
import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any, Type
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, ValidationError, Field

import config

logger = logging.getLogger("agent.llm")

class FillFormField(BaseModel):
    id: str = Field(description="The e12 ID of the field to type into")
    value: str = Field(description="The text to type")

class ActionArgs(BaseModel):
    command: str | None = None
    url: str | None = None
    selector_id: str | None = None
    text: str | None = None
    key: str | None = None
    summary: str | None = None
    key_name: str | None = None
    value: str | None = None
    question: str | None = None
    action_name: str | None = None
    method: str | None = None
    headers: str | None = None
    body: str | None = None
    file_path: str | None = None
    invoice_id: str | None = None
    vendor: str | None = None
    due_date: str | None = None
    amount: str | None = None
    notes: str | None = None
    fields: list[FillFormField] | None = None
    submit: str | None = None
    path: str | None = None
    foo: str | None = None

class GeminiResponseSchema(BaseModel):
    thought: str
    action: str
    args: ActionArgs
    expected_outcome: str

class LLMResponse(BaseModel):
    thought: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str

class LLMQuotaExhaustedError(RuntimeError):
    """Raised when all configured LLM chain entries have exhausted their quotas."""
    pass

# Run Metrics
class RunMetrics:
    def __init__(self):
        self.providers_used: list[str] = []
        self.models_used: list[str] = []
        self.model_switches: int = 0
        self.rate_limit_429_count: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_latency_ms: int = 0
        self.successful_calls: int = 0
        self.status: str = "ok"  # "ok", "infra_error", "llm_quota_exhausted"

    def record_success(self, provider: str, model: str, tokens: dict, latency_ms: int):
        if provider not in self.providers_used:
            self.providers_used.append(provider)
        if model not in self.models_used:
            self.models_used.append(model)
        self.total_prompt_tokens += tokens.get("prompt", 0)
        self.total_completion_tokens += tokens.get("completion", 0)
        self.total_latency_ms += latency_ms
        self.successful_calls += 1

    def record_switch(self):
        self.model_switches += 1

    def record_429(self):
        self.rate_limit_429_count += 1

    def finalize(self):
        if self.successful_calls == 0 and self.status == "ok":
            self.status = "infra_error"

current_run_metrics = RunMetrics()

def reset_run_metrics() -> RunMetrics:
    global current_run_metrics
    current_run_metrics = RunMetrics()
    return current_run_metrics

def get_run_metrics() -> RunMetrics:
    return current_run_metrics

# Persistence in .llm_state.json
def get_next_midnight_pacific() -> float:
    """Calculate the next midnight in US Pacific Time (America/Los_Angeles)."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = timezone(timedelta(hours=-7))
    now = datetime.now(tz)
    tomorrow = now + timedelta(days=1)
    midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()

def load_llm_state(filepath: str = None) -> dict:
    fp = filepath or config.LLM_STATE_FILE
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, "r") as f:
            state = json.load(f)
        # Prune expired entries
        now = time.time()
        active_state = {}
        changed = False
        for k, v in state.items():
            if v.get("expires_at", 0) > now:
                active_state[k] = v
            else:
                changed = True
        if changed:
            save_llm_state(active_state, fp)
        return active_state
    except Exception as e:
        logger.warning(f"Failed to load LLM state from {fp}: {e}")
        return {}

def save_llm_state(state: dict, filepath: str = None):
    fp = filepath or config.LLM_STATE_FILE
    try:
        with open(fp, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save LLM state to {fp}: {e}")

def record_entry_exhaustion(
    provider: str,
    model: str,
    exhaustion_type: str,
    reason: str,
    retry_after: float | None = None,
    filepath: str = None
) -> float:
    """Persist exhaustion for a specific (provider, model) entry with proper expiry."""
    entry_key = f"{provider}/{model}"
    now = time.time()
    if exhaustion_type == "daily":
        if provider == "gemini":
            expires_at = get_next_midnight_pacific()
        elif provider == "groq":
            expires_at = now + 86400.0  # now + 24h
        else:
            expires_at = now + 86400.0
    else:  # per_minute
        wait_sec = retry_after if (retry_after and retry_after > 0) else 60.0
        expires_at = now + wait_sec

    state = load_llm_state(filepath)
    state[entry_key] = {
        "exhausted": True,
        "type": exhaustion_type,
        "reason": reason,
        "expires_at": expires_at,
        "recorded_at": now
    }
    save_llm_state(state, filepath)
    return expires_at

def is_entry_exhausted(provider: str, model: str, filepath: str = None) -> tuple[bool, str, float]:
    """Check if an entry is currently recorded as exhausted. Returns (is_exhausted, reason, remaining_sec)."""
    entry_key = f"{provider}/{model}"
    state = load_llm_state(filepath)
    info = state.get(entry_key)
    if not info:
        return False, "", 0.0
    rem = info.get("expires_at", 0) - time.time()
    if rem > 0:
        return True, info.get("reason", "exhausted"), rem
    return False, "", 0.0

# 429 Classification and Error Extraction
def extract_raw_error_body(error: Exception) -> str:
    """Extract full raw error body from API client exceptions."""
    if hasattr(error, "response") and error.response is not None:
        if hasattr(error.response, "text") and error.response.text:
            return str(error.response.text)
    if hasattr(error, "message") and error.message:
        return str(error.message)
    return str(error)

def parse_retry_after(error: Exception, raw_text: str = None) -> float | None:
    """Extract retry-after delay in seconds from headers, JSON body, or error text."""
    if hasattr(error, "response") and getattr(error, "response", None) is not None:
        headers = getattr(error.response, "headers", {})
        ra = headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
    msg = raw_text or extract_raw_error_body(error)
    # Check for retryDelay in json details: e.g. 'retryDelay': '4s'
    delay_match = re.search(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s?['\"]", msg)
    if delay_match:
        return float(delay_match.group(1))
    # Check regex: e.g. "try again in 12s", "retry in 4.27s", "Please retry in 4s"
    match = re.search(r"(?:retry|try again) in (?:(\d+)m)?(\d+(?:\.\d+)?)s", msg, re.IGNORECASE)
    if match:
        m, s = match.groups()
        sec = float(s)
        if m:
            sec += float(m) * 60
        return sec
    return None

def classify_429(provider: str, error: Exception) -> tuple[str, float | None, str]:
    """
    On any 429, print the raw error body once.
    Classify using Google's quotaId/quotaMetric when present ("PerDay" = daily, "PerMinute" = per-minute);
    for Groq use TPD/RPD vs TPM/RPM.
    Returns (exhaustion_type: 'daily' | 'per_minute', retry_after: float | None, reason: str).
    """
    raw_str = extract_raw_error_body(error)
    print(f"\n[429 RAW ERROR BODY - {provider}]:\n{raw_str}\n", flush=True)

    retry_after = parse_retry_after(error, raw_str)

    if provider == "gemini":
        quota_id_match = re.search(r"['\"]quotaId['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw_str, re.IGNORECASE)
        quota_metric_match = re.search(r"['\"]quotaMetric['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw_str, re.IGNORECASE)

        quota_id = quota_id_match.group(1) if quota_id_match else ""
        quota_metric = quota_metric_match.group(1) if quota_metric_match else ""
        combined_quota = f"{quota_id} {quota_metric}".lower()

        if combined_quota.strip():
            if "perday" in combined_quota or "per_day" in combined_quota:
                return "daily", retry_after, f"Gemini quotaId: {quota_id or quota_metric} (PerDay)"
            if "perminute" in combined_quota or "per_minute" in combined_quota:
                return "per_minute", retry_after, f"Gemini quotaId: {quota_id or quota_metric} (PerMinute)"

        # Fallback keyword checks if quotaId wasn't in json details
        lower_raw = raw_str.lower()
        if "perday" in lower_raw or "per day" in lower_raw or "requestsperday" in lower_raw or "tokensperday" in lower_raw:
            return "daily", retry_after, "Gemini error matched PerDay"
        if "perminute" in lower_raw or "per minute" in lower_raw or "requestsperminute" in lower_raw:
            return "per_minute", retry_after, "Gemini error matched PerMinute"

        # If quota details absent, default to per_minute if retry-after is small, else daily
        if retry_after is not None:
            return "per_minute", retry_after, f"Gemini retryDelay {retry_after}s"
        return "daily", retry_after, "Gemini 429 daily quota"

    elif provider == "groq":
        msg = raw_str.upper()
        has_tpd_rpd = ("TPD" in msg or "RPD" in msg or "TOKENS PER DAY" in msg or "REQUESTS PER DAY" in msg)
        has_tpm_rpm = ("TPM" in msg or "RPM" in msg or "TOKENS PER MINUTE" in msg or "REQUESTS PER MINUTE" in msg)

        if has_tpd_rpd:
            return "daily", retry_after, "Groq TPD/RPD quota reached"
        if has_tpm_rpm:
            return "per_minute", retry_after, "Groq TPM/RPM rate limit"

        return "per_minute", retry_after, "Groq 429 rate limit"

    elif provider == "openrouter":
        lower_raw = raw_str.lower()
        if "daily" in lower_raw or "quota" in lower_raw or "credit" in lower_raw:
            return "daily", retry_after, "OpenRouter daily/credit quota reached"
        return "per_minute", retry_after, "OpenRouter per-minute rate limit"

    return "per_minute", retry_after, "Generic 429"

# Provider Models Cache & Startup Filter
_SUPPORTED_MODELS_CACHE: dict[str, set[str]] = {}

def get_supported_models(provider: str) -> set[str]:
    """Fetch and cache available models via provider's models.list()."""
    if provider in _SUPPORTED_MODELS_CACHE:
        return _SUPPORTED_MODELS_CACHE[provider]

    models: set[str] = set()
    if provider == "gemini":
        if config.GEMINI_API_KEY:
            try:
                from google import genai
                client = genai.Client(api_key=config.GEMINI_API_KEY)
                for m in client.models.list():
                    models.add(m.name.replace("models/", ""))
            except Exception as e:
                logger.warning(f"[Gemini] Failed to list models: {e}")
    elif provider == "groq":
        if config.GROQ_API_KEY:
            try:
                from groq import Groq
                client = Groq(api_key=config.GROQ_API_KEY)
                for m in client.models.list().data:
                    models.add(m.id)
            except Exception as e:
                logger.warning(f"[Groq] Failed to list models: {e}")
    elif provider == "openrouter":
        if config.OPENROUTER_API_KEY:
            try:
                import requests
                headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
                r = requests.get(f"{config.OPENROUTER_BASE_URL}/models", headers=headers, timeout=10)
                if r.status_code == 200:
                    for m in r.json().get("data", []):
                        models.add(m["id"])
            except Exception as e:
                logger.warning(f"[OpenRouter] Failed to list models: {e}")

    _SUPPORTED_MODELS_CACHE[provider] = models
    return models

# Chain Entry Data Structure
class ChainEntry:
    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self.is_exhausted = False
        self.exhaustion_reason = ""
        self.exhaustion_expires_at = 0.0
        self.last_call_timestamp = 0.0

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def rpm_limit(self) -> int:
        if self.provider == "gemini":
            return config.GEMINI_RPM_LIMIT
        elif self.provider == "groq":
            return config.GROQ_RPM_LIMIT
        elif self.provider == "openrouter":
            return config.OPENROUTER_RPM_LIMIT
        return 15

    async def pace(self):
        min_interval = 60.0 / max(1, self.rpm_limit)
        elapsed = time.time() - self.last_call_timestamp
        if elapsed < min_interval:
            wait_time = min_interval - elapsed
            await asyncio.sleep(wait_time)
        self.last_call_timestamp = time.time()

# Provider Adapters
async def call_gemini(model: str, messages: list[dict], schema: Type[BaseModel]) -> tuple[BaseModel, dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    system_instruction = ""
    contents = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            system_instruction = (system_instruction + "\n" + content).strip()
        elif role == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=content)]))
        else:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=content)]))

    gen_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=GeminiResponseSchema,
        system_instruction=system_instruction if system_instruction else None
    )

    start_time = time.time()
    resp = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=gen_config
    )
    latency_ms = int((time.time() - start_time) * 1000)

    raw_text = resp.text
    parsed_gemini = GeminiResponseSchema.model_validate_json(raw_text)
    args_dict = {k: v for k, v in parsed_gemini.args.model_dump().items() if v is not None}
    validated = LLMResponse(
        thought=parsed_gemini.thought,
        action=parsed_gemini.action,
        args=args_dict,
        expected_outcome=parsed_gemini.expected_outcome
    )
    prompt_tokens = resp.usage_metadata.prompt_token_count if resp.usage_metadata else 0
    comp_tokens = resp.usage_metadata.candidates_token_count if resp.usage_metadata else 0

    return validated, {
        "tokens": {"prompt": prompt_tokens, "completion": comp_tokens},
        "latency_ms": latency_ms,
        "provider": "gemini",
        "model": model
    }

async def call_groq(model: str, messages: list[dict], schema: Type[BaseModel]) -> tuple[BaseModel, dict]:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"}
    }
    # Only gpt-oss supports reasoning_effort
    if "gpt-oss" in model.lower():
        kwargs["reasoning_effort"] = "low"
        kwargs["max_completion_tokens"] = 400
    elif "qwen" in model.lower() or "deepseek" in model.lower():
        kwargs["max_completion_tokens"] = 400

    start_time = time.time()
    
    for attempt in range(2):
        resp = await client.chat.completions.create(**kwargs)
        
        choice = resp.choices[0]
        content = choice.message.content
        finish_reason = getattr(choice, "finish_reason", "")
        
        if not content or finish_reason == "length":
            if attempt == 0:
                kwargs["max_completion_tokens"] = kwargs.get("max_completion_tokens", 400) * 2
                continue
            else:
                raise Exception(f"Groq API returned empty content or hit length limit after retry. Finish reason: {finish_reason}")
        break

    latency_ms = int((time.time() - start_time) * 1000)

    parsed = schema.model_validate_json(content)
    u = resp.usage
    prompt_tokens = u.prompt_tokens if u else 0
    comp_tokens = u.completion_tokens if u else 0

    return parsed, {
        "tokens": {"prompt": prompt_tokens, "completion": comp_tokens},
        "latency_ms": latency_ms,
        "provider": "groq",
        "model": model
    }

async def call_openrouter(model: str, messages: list[dict], schema: Type[BaseModel]) -> tuple[BaseModel, dict]:
    import httpx
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/shekhar-narayan-mishra/Autonomous-Ai-Worker",
        "X-Title": "Autonomous AI Worker",
    }
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"}
    }
    start_time = time.time()
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        res = await http_client.post(f"{config.OPENROUTER_BASE_URL}/chat/completions", headers=headers, json=payload)
        latency_ms = int((time.time() - start_time) * 1000)

        if res.status_code == 429:
            raise RuntimeError(f"OpenRouter 429 Rate Limit: {res.text}")
        elif not res.is_success:
            raise RuntimeError(f"OpenRouter API error {res.status_code}: {res.text}")

        data = res.json()
        content = data["choices"][0]["message"]["content"]
        parsed = schema.model_validate_json(content)
        u = data.get("usage", {})
        prompt_tokens = u.get("prompt_tokens", 0)
        comp_tokens = u.get("completion_tokens", 0)

        return parsed, {
            "tokens": {"prompt": prompt_tokens, "completion": comp_tokens},
            "latency_ms": latency_ms,
            "provider": "openrouter",
            "model": model
        }

# Chain Manager
class LLMChainManager:
    def __init__(self, raw_entries: list[tuple[str, str]] = None, check_models_list: bool = False):
        self.raw_entries = raw_entries or config.CHAIN_ENTRIES
        self.entries: list[ChainEntry] = []
        self._init_chain(check_models_list=check_models_list)

    def _init_chain(self, check_models_list: bool = False):
        for provider, model in self.raw_entries:
            # Only check models.list() when explicitly requested (requires network)
            if check_models_list:
                supported = get_supported_models(provider)
                if supported and model not in supported:
                    print(f"[LLM Chain] Dropping {provider}/{model} (not in provider's models.list())", flush=True)
                    continue

            entry = ChainEntry(provider, model)
            # Check persisted state (disk only, no network)
            is_ex, reason, rem = is_entry_exhausted(provider, model)
            if is_ex:
                entry.is_exhausted = True
                entry.exhaustion_reason = reason
                entry.exhaustion_expires_at = time.time() + rem
                logger.info(f"[LLM Chain] {provider}/{model} loaded as EXHAUSTED (reason: {reason}, expires in {int(rem)}s)")

            self.entries.append(entry)

    def get_active_entry(self) -> ChainEntry | None:
        now = time.time()
        for e in self.entries:
            if e.is_exhausted:
                if e.exhaustion_expires_at > 0 and now >= e.exhaustion_expires_at:
                    e.is_exhausted = False
                    e.exhaustion_reason = ""
                    return e
            else:
                return e
        return None

    def mark_exhausted(self, entry: ChainEntry, exhaustion_type: str, reason: str, retry_after: float | None = None):
        entry.is_exhausted = True
        entry.exhaustion_reason = reason
        expires_at = record_entry_exhaustion(entry.provider, entry.model, exhaustion_type, reason, retry_after)
        entry.exhaustion_expires_at = expires_at
        current_run_metrics.record_switch()
        logger.warning(f"[LLM Chain] Marked '{entry.key}' as EXHAUSTED (type: {exhaustion_type}, reason: {reason})")

# Lazy singleton — created on first use via _get_chain_manager(), not at import time
_chain_manager: LLMChainManager | None = None

def _get_chain_manager() -> LLMChainManager:
    """Return the active chain manager, creating the default one on first use (no network calls)."""
    global _chain_manager
    if _chain_manager is None:
        _chain_manager = LLMChainManager(check_models_list=False)
    return _chain_manager

def set_chain_manager(custom_manager: LLMChainManager):
    global _chain_manager
    _chain_manager = custom_manager

# Alias kept for backward compatibility with tests that import chain_manager directly
# Tests that need isolation should use set_chain_manager()
def _compat_chain_manager():
    return _get_chain_manager()

# Backward-compatible Provider interface for tests
class LLMProvider(ABC):
    name: str
    model_name: str
    rpm_limit: int = 15
    is_exhausted: bool = False
    last_call_timestamp: float = 0.0

    async def pace(self):
        min_interval = 60.0 / max(1, self.rpm_limit)
        elapsed = time.time() - self.last_call_timestamp
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self.last_call_timestamp = time.time()

    @abstractmethod
    async def generate_action(self, messages: list[dict], schema: Type[BaseModel] = LLMResponse) -> tuple[BaseModel, dict]:
        pass

# Main Action Generation Entrypoint
async def generate_action(
    system_prompt_or_messages: str | list[dict],
    history: list[dict] = None,
    max_retries: int = 1,
    schema: Type[BaseModel] = LLMResponse
) -> tuple[LLMResponse, dict]:
    """
    Main entry point for generating structured actions across the LLM chain.
    Mid-run switch preserves full conversation messages and schema.
    """
    if isinstance(system_prompt_or_messages, list):
        messages = list(system_prompt_or_messages)
    else:
        messages = [{"role": "system", "content": system_prompt_or_messages}] + (history or [])

    mgr = _get_chain_manager()

    while True:
        entry = mgr.get_active_entry()
        if not entry:
            current_run_metrics.status = "llm_quota_exhausted"
            current_run_metrics.finalize()
            raise LLMQuotaExhaustedError("All configured LLM chain entries are exhausted (status: llm_quota_exhausted).")

        working_messages = list(messages)
        last_error = ""

        # Try on current entry
        for attempt in range(max_retries + 1):
            if attempt > 0:
                working_messages.append({
                    "role": "user",
                    "content": f"Failed to parse JSON on previous attempt:\n{last_error}\nPlease fix and output valid JSON."
                })

            await entry.pace()

            try:
                if entry.provider == "gemini":
                    action, usage = await call_gemini(entry.model, working_messages, schema=schema)
                elif entry.provider == "groq":
                    action, usage = await call_groq(entry.model, working_messages, schema=schema)
                elif entry.provider == "openrouter":
                    action, usage = await call_openrouter(entry.model, working_messages, schema=schema)
                else:
                    raise RuntimeError(f"Unknown provider '{entry.provider}'")

                current_run_metrics.record_success(entry.provider, entry.model, usage["tokens"], usage["latency_ms"])
                return action, usage

            except ValidationError as ve:
                last_error = str(ve)
                continue

            except Exception as e:
                err_str = str(e)
                if "json_validate_failed" in err_str or "Failed to validate JSON" in err_str:
                    last_error = err_str
                    continue

                if "429" in err_str or "rate limit" in err_str.lower() or "resource_exhausted" in err_str.lower():
                    current_run_metrics.record_429()
                    ex_type, ra, reason = classify_429(entry.provider, e)

                    if ex_type == "daily":
                        mgr.mark_exhausted(entry, "daily", reason, retry_after=ra)
                        break  # Break inner loop, move to next entry
                    else:  # per_minute
                        wait_sec = ra if (ra and ra > 0) else min(30.0, (2 ** attempt) + random.uniform(0.1, 0.5))
                        if attempt < max_retries:
                            logger.warning(f"[MultiProvider] Entry '{entry.key}' per-minute 429. Backing off {wait_sec:.1f}s before retry...")
                            await asyncio.sleep(wait_sec)
                            last_error = str(e)
                            continue
                        else:
                            # Retries exhausted for this turn, mark per-minute in state and advance
                            mgr.mark_exhausted(entry, "per_minute", reason, retry_after=wait_sec)
                            break

                elif "503" in err_str or "unavailable" in err_str.lower() or "timeout" in err_str.lower():
                    if attempt < max_retries:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' transient 503/timeout error. Retrying in 2s...")
                        await asyncio.sleep(2)
                        last_error = str(e)
                        continue
                    else:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' error persisted: {e}. Switching to next entry...")
                        mgr.mark_exhausted(entry, "per_minute", f"503 persisted: {e}", retry_after=300)
                        break

                else:
                    # Other errors (e.g. 404 deprecated model, 400 invalid request)
                    logger.warning(f"[MultiProvider] Entry '{entry.key}' error: {e}. Switching to next entry...")
                    mgr.mark_exhausted(entry, "daily", f"Error: {e}")
                    break

