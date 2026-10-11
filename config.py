import json
import os

import dotenv

dotenv.load_dotenv()

# Default LLM Chain: list of (provider, model) tuples
DEFAULT_CHAIN = [
    ("gemini", "gemini-3.8-flash"),
    ("gemini", "gemini-3.5-flash-lite"),
    ("gemini", "gemini-3.5-flash"),
    ("gemini", "gemini-flash-latest"),
    ("groq", "openai/gpt-oss-120b"),
    ("groq", "qwen/qwen3.8-27b"),
    ("groq", "openai/gpt-oss-20b"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("openrouter", "google/gemma-4-31b-it:free")
]

# Parse chain from environment if overridden
raw_chain = os.getenv("LLM_CHAIN_ORDER") or os.getenv("LLM_CHAIN") or os.getenv("PROVIDER_CHAIN")
if raw_chain:
    try:
        parsed = json.loads(raw_chain)
        if isinstance(parsed, list):
            CHAIN_ENTRIES = []
            for item in parsed:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    CHAIN_ENTRIES.append((str(item[0]), str(item[1])))
                elif isinstance(item, str) and "/" in item:
                    parts = item.split("/", 1)
                    CHAIN_ENTRIES.append((parts[0], parts[1]))
        else:
            CHAIN_ENTRIES = list(DEFAULT_CHAIN)
    except Exception:
        entries = []
        for part in raw_chain.split(","):
            part = part.strip()
            if "/" in part:
                p, m = part.split("/", 1)
                entries.append((p.strip(), m.strip()))
        CHAIN_ENTRIES = entries if entries else list(DEFAULT_CHAIN)
else:
    CHAIN_ENTRIES = list(DEFAULT_CHAIN)

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Proactive pacing RPM limits
GEMINI_RPM_LIMIT = int(os.getenv("GEMINI_RPM_LIMIT", "6"))
GROQ_RPM_LIMIT = int(os.getenv("GROQ_RPM_LIMIT", "30"))
OPENROUTER_RPM_LIMIT = int(os.getenv("OPENROUTER_RPM_LIMIT", "15"))

# State persistence file
LLM_STATE_FILE = os.getenv("LLM_STATE_FILE", ".llm_state.json")

# Environment Profiles
def get_environment_file() -> str:
    env_file = os.getenv("ENVIRONMENT_FILE")
    if env_file and os.path.exists(env_file):
        return env_file
    if os.path.exists("config/environment.yaml"):
        return "config/environment.yaml"
    if os.path.exists("config/environment.local.yaml"):
        return "config/environment.local.yaml"
    return "config/environment.yaml"

def load_environment() -> dict:
    import yaml
    env_path = get_environment_file()
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}

# Runtime Safety and Budgets
RUN_TOKEN = os.getenv("RUN_TOKEN", "")
DEMO_MODE = os.getenv("DEMO_MODE", "")  # e.g. "replay_only"
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))
WALL_TIMEOUT_CAP = float(os.getenv("WALL_TIMEOUT_CAP", "600"))
TOOL_TIMEOUT_CAP = float(os.getenv("TOOL_TIMEOUT_CAP", "20"))

# Backward compatibility
PROVIDER_CHAIN = CHAIN_ENTRIES
MODEL_NAME = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-120b"

