"""
ReportVerifier — independent reconciliation verifier.

Does NOT read the agent trace to determine correctness.
Instead, queries both source (vendor portal) and destination (ERP) DBs,
computes the true mismatch set, then checks whether the agent's final
summary mentioned each mismatched invoice.

This ensures the agent cannot game verification by constructing a
convincing-sounding summary string.
"""
from __future__ import annotations

import json
import sqlite3

from agent.verifier import BaseVerifier, CheckResult, VerifierResult


class ReportVerifier(BaseVerifier):
    def verify(self, task_args: dict) -> VerifierResult:
        checks: list[CheckResult] = []
        evidence: dict = {}

        # ------------------------------------------------------------------ #
        # Step 1: Load ground truth from both DBs independently
        # ------------------------------------------------------------------ #
        try:
            portal_db = sqlite3.connect("mock_env/vendor_portal/data.db")
            portal_db.row_factory = sqlite3.Row
            portal_invoices = {
                row["id"]: {"amount": float(row["amount"]), "due_date": row["due_date"]}
                for row in portal_db.execute("SELECT * FROM invoices")
            }
            portal_db.close()
        except Exception as exc:
            checks.append(CheckResult(name="Portal DB access", passed=False, details=str(exc)))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        try:
            erp_db = sqlite3.connect("mock_env/erp/erp.db")
            erp_db.row_factory = sqlite3.Row
            erp_bills = {
                row["invoice_id"]: {"amount": float(row["amount"]), "due_date": row["due_date"]}
                for row in erp_db.execute("SELECT * FROM bills")
            }
            erp_db.close()
        except Exception as exc:
            checks.append(CheckResult(name="ERP DB access", passed=False, details=str(exc)))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        evidence["portal_invoices"] = portal_invoices
        evidence["erp_bills"] = erp_bills

        # ------------------------------------------------------------------ #
        # Step 2: Compute true mismatch set
        # ------------------------------------------------------------------ #
        true_mismatches: list[str] = []
        for inv_id, portal_data in portal_invoices.items():
            if inv_id not in erp_bills:
                # Missing from ERP — if the task was reconciliation, this is a mismatch
                true_mismatches.append(inv_id)
                continue
            erp_data = erp_bills[inv_id]
            if erp_data["amount"] != portal_data["amount"] or erp_data["due_date"] != portal_data["due_date"]:
                true_mismatches.append(inv_id)

        evidence["true_mismatches"] = true_mismatches
        checks.append(CheckResult(
            name="Mismatch set computed",
            passed=True,
            details=f"True mismatches: {true_mismatches}"
        ))

        # ------------------------------------------------------------------ #
        # Step 3: Read agent's final summary from trace (for coverage check)
        #         — but only as a secondary check, not the primary source of truth
        # ------------------------------------------------------------------ #
        agent_summary = ""
        try:
            import os
            if os.path.exists("trace.jsonl"):
                with open("trace.jsonl", "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("action") == "finish":
                                agent_summary = str(data.get("args", {}).get("summary", ""))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass

        evidence["agent_summary"] = agent_summary

        # ------------------------------------------------------------------ #
        # Step 4: Verify the agent mentioned each true mismatch in its summary
        # ------------------------------------------------------------------ #
        if not true_mismatches:
            # No mismatches exist — agent should report nothing or say "all match"
            checks.append(CheckResult(
                name="No mismatches to report",
                passed=True,
                details="Portal and ERP are in sync. Agent reporting optional."
            ))
        else:
            for inv_id in true_mismatches:
                mentioned = inv_id.lower() in agent_summary.lower()
                checks.append(CheckResult(
                    name=f"Mismatch {inv_id} mentioned",
                    passed=mentioned,
                    details=(
                        f"Agent summary {'mentions' if mentioned else 'does NOT mention'} {inv_id}."
                        f" True mismatch: portal={portal_invoices.get(inv_id)},"
                        f" ERP={erp_bills.get(inv_id, 'MISSING')}"
                    )
                ))

        # ------------------------------------------------------------------ #
        # Step 5: Check expected keywords if provided (optional secondary check)
        # ------------------------------------------------------------------ #
        for kw in task_args.get("expected_in_summary", []):
            present = kw.lower() in agent_summary.lower()
            checks.append(CheckResult(
                name=f"Keyword '{kw}' in summary",
                passed=present,
                details=f"Summary {'contains' if present else 'missing'} keyword '{kw}'."
            ))

        return VerifierResult(
            passed=all(c.passed for c in checks),
            checks=checks,
            evidence=evidence,
        )
