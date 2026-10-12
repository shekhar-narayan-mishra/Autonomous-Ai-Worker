import json
import os

CHAOS_FILE = "chaos_state.json"

def get_chaos():
    if not os.path.exists(CHAOS_FILE):
        return {}
    try:
        with open(CHAOS_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception:
        return {}

def set_chaos(flags):
    with open(CHAOS_FILE, "w") as f:
        json.dump(flags, f)

def is_active(flag_name):
    return get_chaos().get(flag_name, False)
