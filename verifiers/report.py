import json
from agent.verifier import BaseVerifier, VerifierResult, CheckResult

class ReportVerifier(BaseVerifier):
    def verify(self, task_args: dict) -> VerifierResult:
        expected_keywords = task_args.get("expected_in_summary", [])
        
        # Read the trace to find the final summary
        summary = ""
        try:
            with open("trace.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    data = json.loads(line)
                    if data.get("action") == "finish":
                        summary = str(data.get("args", {}).get("summary", ""))
        except Exception as e:
            return VerifierResult(passed=False, checks=[CheckResult(name="Read trace", passed=False, details=str(e))], evidence={})
            
        checks = []
        for kw in expected_keywords:
            passed = kw.lower() in summary.lower()
            checks.append(CheckResult(
                name=f"Keyword '{kw}'", 
                passed=passed, 
                details=f"Summary {'contains' if passed else 'missing'} keyword '{kw}'."
            ))
            
        return VerifierResult(
            passed=all(c.passed for c in checks), 
            checks=checks, 
            evidence={"summary": summary}
        )
