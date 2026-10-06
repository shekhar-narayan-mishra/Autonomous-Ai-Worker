import asyncio
import os
import requests
import config

def get_openrouter():
    headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
    r = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=10)
    os.makedirs("docs", exist_ok=True)
    with open("docs/free_models.txt", "w") as f:
        if r.status_code == 200:
            for m in r.json().get("data", []):
                if m.get("pricing", {}).get("prompt", "") == "0" or ":free" in m["id"]:
                    f.write(f"{m['id']}\n")

def get_groq():
    from groq import Groq
    client = Groq(api_key=config.GROQ_API_KEY)
    os.makedirs("docs", exist_ok=True)
    with open("docs/groq_models.txt", "w") as f:
        for m in client.models.list().data:
            f.write(f"{m.id}\n")

get_openrouter()
get_groq()
