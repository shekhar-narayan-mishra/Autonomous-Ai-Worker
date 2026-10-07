import json
import time


class TraceLogger:
    """Write execution steps as JSONL entries. Tokens are always stored as dict."""

    def __init__(self, path: str = "trace.jsonl"):
        self.path = path

    def log_step(
        self,
        step: int,
        thought: str,
        action: str,
        args: dict,
        observation: str,
        ok: bool,
        tokens: dict,
        llm_inference_ms: int,
        llm_wait_ms: int,
        tool_ms: int,
        screenshot_path: str = None,
        attempts: list = None,
    ) -> None:
        # Normalise tokens — always store as {"prompt": N, "completion": N}
        if isinstance(tokens, dict):
            tok_entry = {
                "prompt": int(tokens.get("prompt", 0)),
                "completion": int(tokens.get("completion", 0)),
            }
        elif isinstance(tokens, (int, float)):
            tok_entry = {"prompt": int(tokens), "completion": 0}
        else:
            tok_entry = {"prompt": 0, "completion": 0}

        entry = {
            "step": step,
            "timestamp": time.time(),
            "thought": thought,
            "action": action,
            "args": args,
            "attempts": attempts or [],
            "observation_summary": (
                observation[:200] + "..." if len(observation) > 200 else observation
            ),
            "ok": ok,
            "tokens": tok_entry,
            "llm_inference_ms": int(llm_inference_ms) if llm_inference_ms else 0,
            "llm_wait_ms": int(llm_wait_ms) if llm_wait_ms else 0,
            "tool_ms": int(tool_ms) if tool_ms else 0,
            "screenshot": screenshot_path,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
