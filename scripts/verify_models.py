import config
import os

with open("docs/free_models.txt") as f:
    free_models = {line.strip() for line in f if line.strip()}

with open("docs/groq_models.txt") as f:
    groq_models = {line.strip() for line in f if line.strip()}

gemini_models = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-latest"}

for provider, model in config.CHAIN_ENTRIES:
    if provider == "gemini":
        assert model in gemini_models, f"Missing {model}"
    elif provider == "groq":
        assert model in groq_models, f"Missing {model}"
    elif provider == "openrouter":
        assert model in free_models, f"Missing {model}"
    print(f"VERIFIED: {provider} / {model}")
print("ALL CHAIN ENTRIES VERIFIED.")
