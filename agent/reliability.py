import time
from enum import Enum
from pydantic import BaseModel

class ErrorClassification(str, Enum):
    TRANSIENT = "transient"
    WRONG_APPROACH = "wrong_approach"
    BLOCKED = "blocked"
    SUCCESS = "success"

class ReliabilityManager:
    def __init__(self, max_steps=20, max_wall_time=300):
        self.max_steps = max_steps
        self.max_wall_time = max_wall_time
        self.start_time = time.time()
        self.action_history = []
        
    def check_budgets(self, current_step: int):
        if current_step > self.max_steps:
            raise Exception("BudgetExhausted: Max steps reached.")
        if time.time() - self.start_time > self.max_wall_time:
            raise Exception("BudgetExhausted: Max wall time reached.")
            
    def record_action(self, action: str, args: dict):
        self.action_history.append({"action": action, "args": str(args)})
        
    def detect_loop(self) -> bool:
        if len(self.action_history) < 3:
            return False
        last_3 = self.action_history[-3:]
        return all(x == last_3[0] for x in last_3)

    def classify_error(self, error: Exception | str) -> ErrorClassification:
        err_str = str(error).lower()
        if any(x in err_str for x in ["timeout", "network", "500", "connection", "rate limit", "429"]):
            return ErrorClassification.TRANSIENT
        if "not found" in err_str or "invalid" in err_str or "unsupported" in err_str:
            return ErrorClassification.WRONG_APPROACH
        return ErrorClassification.BLOCKED
