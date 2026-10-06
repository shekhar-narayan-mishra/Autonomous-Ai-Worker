import json
import time

class TraceLogger:
    def __init__(self, path="trace.jsonl"):
        self.path = path
        
    def log_step(self, step: int, thought: str, action: str, args: dict, observation: str, ok: bool, tokens: dict, latency: int, screenshot_path: str = None):
        entry = {
            "step": step,
            "timestamp": time.time(),
            "thought": thought,
            "action": action,
            "args": args,
            "observation_summary": observation[:200] + "..." if len(observation) > 200 else observation,
            "ok": ok,
            "tokens": tokens,
            "latency_ms": latency,
            "screenshot": screenshot_path
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
