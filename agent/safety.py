import json
import os

from agent.events import current_event_bus


class HumanInterface:
    def __init__(self):
        self.expected_questions = {}

    def set_expected(self, expected: dict):
        self.expected_questions = expected

    async def ask(self, question: str) -> str:
        bus = current_event_bus.get()
        if bus:
            return await bus.ask(question)
            
        print(f"\n[AGENT ASKS]: {question}")
        import sys
        if os.getenv("EVAL_MODE") == "1" or not sys.stdin.isatty():
            q_lower = question.lower()
            for key, ans in self.expected_questions.items():
                if key.lower() in q_lower:
                    return ans
            if os.getenv("EVAL_MODE") == "1" and "PYTEST_CURRENT_TEST" not in os.environ:
                print(f"[TEST RESPONDER] FAILED: Unexpected question: {question}")
                raise ValueError(f"Unexpected question: {question}")
            return "Please proceed or retry."
        return input("Your response: ")
        
    async def request_approval(self, action: str, args: dict, diff: str) -> bool:
        bus = current_event_bus.get()
        if bus:
            return await bus.request_approval(action, args, diff)
            
        auto_approve = os.getenv("AUTO_APPROVE", "false").lower() == "true"
        if auto_approve:
            print(f"\n[AUTO-APPROVE] {action}({args})")
            return True
            
        print(f"\n[APPROVAL REQUIRED] Action: {action}\nArgs: {json.dumps(args, indent=2)}\nDiff/Impact: {diff}")
        import sys
        if os.getenv("EVAL_MODE") == "1" or not sys.stdin.isatty():
            return True
        ans = input("Approve? (y/n): ")
        return ans.lower() == 'y'

global_human_interface = HumanInterface()
