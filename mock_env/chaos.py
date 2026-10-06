import json
import os

CHAOS_FILE = "chaos_state.json"

def get_chaos():
    if not os.path.exists(CHAOS_FILE):
        return {}
    with open(CHAOS_FILE, "r") as f:
        return json.load(f)

def set_chaos(flags):
    with open(CHAOS_FILE, "w") as f:
        json.dump(flags, f)

def is_active(flag_name):
    return get_chaos().get(flag_name, False)
