"""
Verifier registry and base classes.

get_verifier(name) returns the correct verifier for a task.
Never silently falls back to the wrong verifier — returns None if unknown,
which the loop treats as VERIFICATION_ERROR.
"""
from __future__ import annotations

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
        raise NotImplementedError


_REGISTRY: dict[str, type[BaseVerifier]] = {}


def register_verifier(name: str, cls: type[BaseVerifier]) -> None:
    _REGISTRY[name] = cls


def get_verifier(name: str) -> BaseVerifier | None:
    """Return an instantiated verifier, or None if the name is not registered."""
    cls = _REGISTRY.get(name)
    if cls is None:
        return None
    return cls()


# ---- Register all known verifiers at import time (no network calls) ----

def _register_all() -> None:
    from verifiers.invoice_entry import InvoiceEntryVerifier
    register_verifier("invoice_entry", InvoiceEntryVerifier)

    from verifiers.report import ReportVerifier
    register_verifier("report", ReportVerifier)


_register_all()
