from pydantic import BaseModel

class CheckResult(BaseModel):
    name: str
    passed: bool
    details: str

class VerifierResult(BaseModel):
    passed: bool
    checks: list[CheckResult]
    evidence: dict

class BaseVerifier:
    def verify(self, task_args: dict) -> VerifierResult:
        pass

def get_verifier(name: str) -> BaseVerifier:
    if name == "invoice_entry":
        from verifiers.invoice_entry import InvoiceEntryVerifier
        return InvoiceEntryVerifier()
    return None
