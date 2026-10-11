import asyncio
import json
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

import config

logger = logging.getLogger("agent.llm")

class FillFormField(BaseModel):
    id: str = Field(description="The e12 ID of the field to type into")
    value: str = Field(description="The text to type")

from pydantic import create_model

from agent.tools.base import ToolRegistry, registry


def build_action_args_model(tool_registry: ToolRegistry = registry) -> type[BaseModel]:
    fields = {}
    for tool in tool_registry.get_all():
        if tool.args_schema:
            for fname, f in tool.args_schema.model_fields.items():
                fields[fname] = (f.annotation | None, None)
    return create_model('ActionArgs', **fields)

def get_dynamic_response_schema(tool_registry: ToolRegistry = registry) -> type[BaseModel]:
    ActionArgsDynamic = build_action_args_model(tool_registry)
    from typing import Literal
    action_names = tuple(t.name for t in tool_registry.get_all())
    
    print(f"\n[LLM SCHEMA] Tool list: {action_names}")
    
    if action_names:
        ActionLiteral = Literal[action_names]
    else:
        ActionLiteral = str

    return create_model('DynamicResponseSchema',
        thought=(str, ...),
        action=(ActionLiteral, ...),
        args=(ActionArgsDynamic, ...),
        expected_outcome=(str, ...)
    )

class LLMResponse(BaseModel):
    thought: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str

from enum import Enum

class LLMErrorClass(str, Enum):
    CONFIG_ERROR = "config_error"
    QUOTA_DAILY = "quota_daily"
    QUOTA_MINUTE = "quota_minute"
    TRANSIENT = "transient"
    BAD_REQUEST = "bad_request"

class LLMQuotaExhaustedError(RuntimeError):
    """Raised when all configured LLM chain entries have exhausted their quotas."""

class LLMPreflightError(RuntimeError):
    """Raised when preflight check fails because no configured LLM chain entry is usable."""


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

def classify_failure(provider: str, error: Exception) -> tuple[str, str, float | None]:
    """
    Classify a provider error into:
    - LLMErrorClass.CONFIG_ERROR: ImportError, ModuleNotFoundError, TypeError on client construction, missing API key
    - LLMErrorClass.QUOTA_DAILY: daily quota exhaustion
    - LLMErrorClass.QUOTA_MINUTE: per-minute rate limit
    - LLMErrorClass.TRANSIENT: 5xx, network timeout, connection drops
    - LLMErrorClass.BAD_REQUEST: 400, 401/403 auth, 404 model not found, schema/parsing error
    Returns: (classification: str, reason: str, retry_after: float | None)
    """
    err_str = str(error)
    err_lower = err_str.lower()

    # 1. Config errors (never persisted to .llm_state.json)
    if isinstance(error, (ImportError, ModuleNotFoundError, TypeError)):
        return LLMErrorClass.CONFIG_ERROR.value, f"SDK/Client config error: {error}", None
    if "no module named" in err_lower or "unexpected keyword argument" in err_lower:
        return LLMErrorClass.CONFIG_ERROR.value, f"SDK installation or constructor argument mismatch: {error}", None
    if "api key" in err_lower and ("missing" in err_lower or "not configured" in err_lower or "not set" in err_lower):
        return LLMErrorClass.CONFIG_ERROR.value, f"Missing API key: {error}", None

    # 2. Rate Limits / Quotas (429)
    if "429" in err_str or "rate limit" in err_lower or "resource_exhausted" in err_lower or "quota" in err_lower:
        ex_type, ra, reason = classify_429(provider, error)
        if ex_type == "daily":
            return LLMErrorClass.QUOTA_DAILY.value, reason, ra
        return LLMErrorClass.QUOTA_MINUTE.value, reason, ra

    # 3. Transient Network / Server 5xx
    if any(code in err_str for code in ["502", "503", "504"]) or "unavailable" in err_lower or "timeout" in err_lower or "network" in err_lower or "connection" in err_lower or "remoteprotocolerror" in err_lower:
        return LLMErrorClass.TRANSIENT.value, f"Transient network/server error: {error}", 2.0

    # 4. Bad Request / Auth / Model not found
    return LLMErrorClass.BAD_REQUEST.value, f"Provider error: {error}", None

def run_preflight(raw_entries: list[tuple[str, str]] = None) -> dict:
    """
    PREFLIGHT check at server startup and at run start.
    For each chain entry, verify SDK import and client construction (no network).
    A config_error never marks an entry exhausted and is never persisted to .llm_state.json.
    If no entry is usable, reports clear diagnostic message per entry with fix hint.
    """
    entries_to_check = raw_entries or config.CHAIN_ENTRIES
    results = []
    usable_count = 0

    for provider, model in entries_to_check:
        entry_status = "ready"
        err_msg = ""
        fix_hint = ""

        # Check persistent exhaustion state
        is_ex, reason, rem = is_entry_exhausted(provider, model)
        if is_ex:
            entry_status = "quota_exhausted"
            err_msg = reason
            fix_hint = f"Wait {int(rem)}s for quota to reset"
            results.append({
                "provider": provider,
                "model": model,
                "status": entry_status,
                "error": err_msg,
                "fix_hint": fix_hint,
                "usable": False
            })
            continue

        if provider == "gemini":
            try:
                import google.genai as genai_module
            except (ImportError, ModuleNotFoundError) as e:
                entry_status = "config_error"
                err_msg = f"Missing SDK: {e}"
                fix_hint = "pip install google-genai"
            else:
                if not config.GEMINI_API_KEY:
                    entry_status = "config_error"
                    err_msg = "GEMINI_API_KEY is not configured"
                    fix_hint = "Set GEMINI_API_KEY in .env"
                else:
                    try:
                        from google import genai
                        _ = genai.Client(api_key=config.GEMINI_API_KEY)
                    except Exception as e:
                        entry_status = "config_error"
                        err_msg = f"Client construction failed: {e}"
                        fix_hint = "Verify google-genai version and API key format"

        elif provider == "groq":
            try:
                import groq as groq_module
                from groq import AsyncGroq, Groq
            except (ImportError, ModuleNotFoundError) as e:
                entry_status = "config_error"
                err_msg = f"Missing SDK: {e}"
                fix_hint = "pip install \"groq>=0.13\""
            else:
                if not config.GROQ_API_KEY:
                    entry_status = "config_error"
                    err_msg = "GROQ_API_KEY is not configured"
                    fix_hint = "Set GROQ_API_KEY in .env"
                else:
                    try:
                        _ = AsyncGroq(api_key=config.GROQ_API_KEY)
                    except TypeError as e:
                        entry_status = "config_error"
                        err_msg = f"Client construction failed: {e}"
                        fix_hint = "pip install \"groq>=0.13\" \"httpx>=0.27\""
                    except Exception as e:
                        entry_status = "config_error"
                        err_msg = f"Client construction failed: {e}"
                        fix_hint = "Verify groq client configuration"

        elif provider == "openrouter":
            try:
                import httpx
            except (ImportError, ModuleNotFoundError) as e:
                entry_status = "config_error"
                err_msg = f"Missing SDK: {e}"
                fix_hint = "pip install httpx"
            else:
                if not config.OPENROUTER_API_KEY:
                    entry_status = "config_error"
                    err_msg = "OPENROUTER_API_KEY is not configured"
                    fix_hint = "Set OPENROUTER_API_KEY in .env"
                else:
                    try:
                        _ = httpx.AsyncClient(timeout=60.0)
                    except Exception as e:
                        entry_status = "config_error"
                        err_msg = f"Client construction failed: {e}"
                        fix_hint = "Verify httpx installation"

        elif provider in ["scripted", "fake"]:
            entry_status = "ready"
        else:
            entry_status = "config_error"
            err_msg = f"Unknown provider '{provider}'"
            fix_hint = "Use gemini, groq, or openrouter"

        is_usable = (entry_status == "ready")
        if is_usable:
            usable_count += 1

        results.append({
            "provider": provider,
            "model": model,
            "status": entry_status,
            "error": err_msg,
            "fix_hint": fix_hint,
            "usable": is_usable
        })

    usable = (usable_count > 0)
    error_summary = ""
    if not usable:
        lines = ["[PREFLIGHT FAILED] No configured LLM chain entry is usable:"]
        for r in results:
            lines.append(f" - {r['provider']}/{r['model']}: {r['status']} ({r['error']}). Fix: {r['fix_hint']}")
        error_summary = "\n".join(lines)

    return {
        "usable": usable,
        "usable_count": usable_count,
        "entries": results,
        "error_summary": error_summary
    }

# Provider Models Cache & Startup Filter

_SUPPORTED_MODELS_CACHE: dict[str, set[str]] = {}

def get_supported_models(provider: str) -> set[str]:
    """Fetch and cache available models via provider's models.list() in .llm_state.json."""
    if provider in _SUPPORTED_MODELS_CACHE:
        return _SUPPORTED_MODELS_CACHE[provider]
        
    state = load_llm_state()
    cache_key = f"models_cache_{provider}"
    cache_info = state.get(cache_key)
    now = time.time()
    
    if cache_info and cache_info.get("expires_at", 0) > now:
        models = set(cache_info.get("models", []))
        _SUPPORTED_MODELS_CACHE[provider] = models
        return models

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
                import httpx
                headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
                with httpx.Client(timeout=10) as c:
                    r = c.get(f"{config.OPENROUTER_BASE_URL}/models", headers=headers)
                    if r.status_code == 200:
                        for m in r.json().get("data", []):
                            models.add(m["id"])
            except Exception as e:
                logger.warning(f"[OpenRouter] Failed to list models: {e}")

    _SUPPORTED_MODELS_CACHE[provider] = models
    
    # Save to state with 24h expiry
    state = load_llm_state()
    state[cache_key] = {
        "models": list(models),
        "expires_at": now + 86400.0,
        "recorded_at": now
    }
    save_llm_state(state)
    
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
        self.consecutive_slow_calls = 0

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

    def get_pacing_wait(self) -> float:
        min_interval = 60.0 / max(1, self.rpm_limit)
        elapsed = time.time() - self.last_call_timestamp
        if elapsed < min_interval:
            return min_interval - elapsed
        return 0.0

    def record_call(self):
        self.last_call_timestamp = time.time()

# Provider Adapters
async def call_gemini(model: str, messages: list[dict], schema: type[BaseModel]) -> tuple[BaseModel, dict]:
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

    schema_dict = schema.model_json_schema()
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

    gen_config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=schema_dict,
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
    parsed_gemini = schema.model_validate_json(raw_text)
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

async def call_groq(model: str, messages: list[dict], schema: type[BaseModel]) -> tuple[BaseModel, dict]:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
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

async def call_openrouter(model: str, messages: list[dict], schema: type[BaseModel]) -> tuple[BaseModel, dict]:
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
        "temperature": 0.0,
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
        self.active_entry: ChainEntry | None = None
        self._init_chain(check_models_list=check_models_list)

    @property
    def chain(self) -> list[ChainEntry]:
        return self.entries

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
        # Keep one provider/model for the whole run unless it fails (sticky)
        if self.active_entry is not None:
            if not self.active_entry.is_exhausted:
                return self.active_entry
            if self.active_entry.exhaustion_expires_at > 0 and now >= self.active_entry.exhaustion_expires_at:
                self.active_entry.is_exhausted = False
                self.active_entry.exhaustion_reason = ""
                return self.active_entry

        for e in self.entries:
            if e.is_exhausted:
                if e.exhaustion_expires_at > 0 and now >= e.exhaustion_expires_at:
                    e.is_exhausted = False
                    e.exhaustion_reason = ""
                    if self.active_entry and self.active_entry.key != e.key:
                        logger.info(f"[LLM Chain] Switching provider/model from {self.active_entry.key} to {e.key}")
                    self.active_entry = e
                    return e
            else:
                if self.active_entry and self.active_entry.key != e.key:
                    logger.info(f"[LLM Chain] Switching provider/model from {self.active_entry.key} to {e.key}")
                self.active_entry = e
                return e
        return None

    def mark_exhausted(self, entry: ChainEntry, exhaustion_type: str, reason: str, retry_after: float | None = None):
        entry.is_exhausted = True
        entry.exhaustion_reason = reason
        expires_at = record_entry_exhaustion(entry.provider, entry.model, exhaustion_type, reason, retry_after)
        entry.exhaustion_expires_at = expires_at
        if self.active_entry == entry:
            self.active_entry = None
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

get_chain_manager = _get_chain_manager

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

    def get_pacing_wait(self) -> float:
        min_interval = 60.0 / max(1, self.rpm_limit)
        elapsed = time.time() - self.last_call_timestamp
        if elapsed < min_interval:
            return min_interval - elapsed
        return 0.0

    def record_call(self):
        self.last_call_timestamp = time.time()

    @abstractmethod
    async def generate_action(self, messages: list[dict], schema: type[BaseModel] = LLMResponse) -> tuple[BaseModel, dict]:
        pass

# Main Action Generation Entrypoint
async def generate_action(
    system_prompt_or_messages: str | list[dict],
    history: list[dict] = None,
    max_retries: int = 1,
    schema: type[BaseModel] = LLMResponse
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
    attempts_log = []

    # Inject dynamic schema with Enum if not overridden by test
    if schema is LLMResponse:
        schema = get_dynamic_response_schema()

    total_wait = 0.0
    wait_reasons = set()

    while True:
        entry = mgr.get_active_entry()
        if not entry:
            now = time.time()
            cooling_down = [e for e in mgr.entries if e.is_exhausted and e.exhaustion_expires_at > now]
            if cooling_down:
                shortest = min(cooling_down, key=lambda x: x.exhaustion_expires_at)
                wait_time = shortest.exhaustion_expires_at - now
                if wait_time <= 20.0 and total_wait + wait_time <= 20.0:
                    logger.warning(f"[MultiProvider] All entries exhausted. Waiting {wait_time:.1f}s for '{shortest.key}'...")
                    await asyncio.sleep(wait_time)
                    total_wait += wait_time
                    shortest.is_exhausted = False
                    shortest.exhaustion_reason = ""
                    entry = shortest
                else:
                    current_run_metrics.status = "llm_quota_exhausted"
                    current_run_metrics.finalize()
                    diag = ["All configurations exhausted. Provider statuses:"]
                    for ex_entry in mgr.entries:
                        diag.append(f" - {ex_entry.provider}/{ex_entry.model}: {ex_entry.exhaustion_reason}")
                    diag.append(f"Shortest cooldown is {wait_time:.1f}s (exceeds limit).")
                    diag.append("Action: Check API keys, select a different model, or wait for quotas to reset.")
                    raise LLMQuotaExhaustedError("\n".join(diag))
            else:
                current_run_metrics.status = "llm_quota_exhausted"
                current_run_metrics.finalize()
                diag = ["All configured LLM chain entries are exhausted. Provider statuses:"]
                for ex_entry in mgr.entries:
                    diag.append(f" - {ex_entry.provider}/{ex_entry.model}: {ex_entry.exhaustion_reason}")
                diag.append("Action: Check API keys and configured models.")
                raise LLMQuotaExhaustedError("\n".join(diag))

        working_messages = list(messages)
        last_error = ""

        # Try on current entry
        for attempt in range(max_retries + 1):
            if attempt > 0:
                working_messages.append({
                    "role": "user",
                    "content": f"Failed to parse JSON on previous attempt:\n{last_error}\nPlease fix and output valid JSON."
                })

            wait_sec = entry.get_pacing_wait()
            if total_wait + wait_sec > 20.0:
                logger.warning(f"[MultiProvider] Entry '{entry.key}' pacing wait ({wait_sec:.1f}s) exceeds total limit. Failing over...")
                mgr.mark_exhausted(entry, "per_minute", "Pacing limit exceeded 20s", retry_after=wait_sec)
                break
                
            if wait_sec > 0:
                await asyncio.sleep(wait_sec)
                total_wait += wait_sec
                wait_reasons.add("pacing")
                
            entry.record_call()

            try:
                if entry.provider == "gemini":
                    action, usage = await call_gemini(entry.model, working_messages, schema=schema)
                elif entry.provider == "groq":
                    action, usage = await call_groq(entry.model, working_messages, schema=schema)
                elif entry.provider == "openrouter":
                    action, usage = await call_openrouter(entry.model, working_messages, schema=schema)
                else:
                    raise RuntimeError(f"Unknown provider '{entry.provider}'")

                attempts_log.append({
                    "provider": entry.provider,
                    "model": entry.model,
                    "outcome": "success",
                    "wait_sec": wait_sec
                })
                
                usage["attempts"] = attempts_log
                usage["wait_ms"] = int(total_wait * 1000)
                usage["wait_reasons"] = list(wait_reasons)
                current_run_metrics.record_success(entry.provider, entry.model, usage["tokens"], usage["latency_ms"])
                if not isinstance(action, LLMResponse) and hasattr(action, 'args'):
                    args_dict = {}
                    if hasattr(action.args, 'model_dump'):
                        args_dict = {k: v for k, v in action.args.model_dump().items() if v is not None}
                    elif hasattr(action.args, 'dict'):
                        args_dict = {k: v for k, v in action.args.dict().items() if v is not None}
                    else:
                        args_dict = dict(action.args)
                    action = LLMResponse(
                        thought=getattr(action, "thought", ""),
                        action=getattr(action, "action", ""),
                        args=args_dict,
                        expected_outcome=getattr(action, "expected_outcome", "")
                    )

                if usage.get("latency_ms", 0) > 10000:
                    entry.consecutive_slow_calls += 1
                else:
                    entry.consecutive_slow_calls = 0

                if entry.consecutive_slow_calls >= 2:
                    logger.warning(f"[MultiProvider] Entry '{entry.key}' exceeded 10s latency on 2 consecutive calls. Failing over for next steps.")
                    mgr.mark_exhausted(entry, "per_minute", "Latency > 10s on 2 consecutive steps", retry_after=120)

                return action, usage
            except ValidationError as ve:
                last_error = str(ve)
                attempts_log.append({
                    "provider": entry.provider,
                    "model": entry.model,
                    "outcome": "validation error",
                    "wait_sec": wait_sec
                })
                continue

            except Exception as e:
                err_str = str(e)
                if "json_validate_failed" in err_str or "Failed to validate JSON" in err_str:
                    last_error = err_str
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": "json parse error",
                        "wait_sec": wait_sec
                    })
                    continue

                failure_class, reason, ra = classify_failure(entry.provider, e)

                if failure_class == LLMErrorClass.CONFIG_ERROR.value:
                    # CRITICAL: A config_error NEVER marks an entry exhausted on disk and is NEVER persisted to .llm_state.json
                    logger.error(f"[MultiProvider] Configuration error on entry '{entry.key}': {reason}. This will NOT be persisted to .llm_state.json.")
                    entry.is_exhausted = True
                    entry.exhaustion_reason = f"Config error: {reason}"
                    entry.exhaustion_expires_at = 0.0  # in-memory only!
                    if mgr.active_entry == entry:
                        mgr.active_entry = None
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": f"config_error: {e}",
                        "wait_sec": wait_sec
                    })
                    current_run_metrics.record_switch()
                    break

                elif failure_class == LLMErrorClass.QUOTA_DAILY.value:
                    current_run_metrics.record_429()
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": f"quota_daily: {reason}",
                        "wait_sec": wait_sec
                    })
                    mgr.mark_exhausted(entry, "daily", reason, retry_after=ra)
                    break

                elif failure_class == LLMErrorClass.QUOTA_MINUTE.value:
                    current_run_metrics.record_429()
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": f"quota_minute: {reason}",
                        "wait_sec": wait_sec
                    })
                    backoff = ra if (ra and ra > 0) else min(8.0, (2 ** attempt) + random.uniform(0.1, 0.5))
                    if backoff > 8.0 or (total_wait + backoff > 20.0):
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' retry-after/wait exceeds limit ({backoff:.1f}s). Failing over...")
                        mgr.mark_exhausted(entry, "per_minute", reason, retry_after=backoff)
                        break

                    if attempt < max_retries:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' per-minute quota. Backing off {backoff:.1f}s before retry...")
                        await asyncio.sleep(backoff)
                        total_wait += backoff
                        wait_reasons.add("429")
                        last_error = str(e)
                        continue
                    else:
                        mgr.mark_exhausted(entry, "per_minute", reason, retry_after=backoff)
                        break

                elif failure_class == LLMErrorClass.TRANSIENT.value:
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": "transient (5xx/network/timeout)",
                        "wait_sec": wait_sec
                    })
                    if attempt < max_retries:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' transient network/5xx error. Retrying in 2s...")
                        if total_wait + 2.0 > 20.0:
                            logger.warning(f"[MultiProvider] Entry '{entry.key}' total wait > 20s. Failing over...")
                            mgr.mark_exhausted(entry, "per_minute", "Transient network timeout", retry_after=60.0)
                            break
                        await asyncio.sleep(2)
                        total_wait += 2.0
                        last_error = str(e)
                        continue
                    else:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' transient error persisted: {e}. Switching to next entry...")
                        mgr.mark_exhausted(entry, "per_minute", f"Network error persisted: {e}", retry_after=300)
                        break

                else:  # BAD_REQUEST / Other
                    attempts_log.append({
                        "provider": entry.provider,
                        "model": entry.model,
                        "outcome": f"bad_request: {e}",
                        "wait_sec": wait_sec
                    })
                    err_lower = err_str.lower()
                    if any(code in err_lower for code in ["401", "403", "unauthorized", "api_key_invalid", "invalid_api_key", "forbidden"]):
                        logger.warning(f"[MultiProvider] Provider '{entry.provider}' auth/billing failure: {e}. Disabling provider.")
                        for other_entry in mgr.entries:
                            if other_entry.provider == entry.provider and not other_entry.is_exhausted:
                                mgr.mark_exhausted(other_entry, "daily", f"Provider Auth/Billing Error: {e}")
                        break
                    elif "404" in err_lower or "not found" in err_lower or "does not exist" in err_lower:
                        logger.warning(f"[MultiProvider] Model '{entry.key}' not found/invalid: {e}. Switching to next entry...")
                        mgr.mark_exhausted(entry, "daily", f"Invalid Model: {e}")
                        break
                    else:
                        logger.warning(f"[MultiProvider] Entry '{entry.key}' unhandled error: {e}. Switching to next entry...")
                        mgr.mark_exhausted(entry, "daily", f"Unhandled Error: {e}")
                        break


