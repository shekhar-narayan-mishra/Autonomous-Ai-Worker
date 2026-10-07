"""
InvoiceEntryVerifier — independent cross-DB verification.

Verifies that:
1. For normal entry tasks:   invoice exists in vendor portal, correct one selected,
                             ERP bill exists, amount/date match, no duplicate.
2. For not-found tasks:      no unintended ERP writes occurred.
3. For duplicate detection:  no second bill was added.
4. For ambiguity tasks:      agent asked human (checked by the loop, not here).
"""

import sqlite3
from agent.verifier import BaseVerifier, CheckResult, VerifierResult


class InvoiceEntryVerifier(BaseVerifier):
    def verify(self, task_args: dict) -> VerifierResult:
        vendor_name = task_args.get("vendor_name", "Acme Corp")
        expected_outcome = task_args.get("expected_outcome", "success")

        # ------------------------------------------------------------------ #
        # Load ground truth from vendor portal DB
        # ------------------------------------------------------------------ #
        checks: list[CheckResult] = []
        evidence: dict = {}

        try:
            portal_db = sqlite3.connect("mock_env/vendor_portal/data.db")
            portal_db.row_factory = sqlite3.Row
            truth = portal_db.execute(
                "SELECT * FROM invoices WHERE vendor=?"
                " ORDER BY date(due_date) DESC, id DESC LIMIT 1",
                (vendor_name,),
            ).fetchone()
            portal_db.close()
        except Exception as exc:
            checks.append(CheckResult(name="Portal DB access", passed=False, details=str(exc)))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        # ------------------------------------------------------------------ #
        # NOT_FOUND / WRONG_VENDOR case
        # ------------------------------------------------------------------ #
        if not truth:
            # Correct behaviour: agent should have reported NOT_FOUND,
            # and must NOT have written anything to ERP.
            if expected_outcome == "must_report_not_found":
                try:
                    erp_db = sqlite3.connect("mock_env/erp/erp.db")
                    erp_db.row_factory = sqlite3.Row
                    # Any bill for this vendor would be an error
                    spurious = erp_db.execute(
                        "SELECT * FROM bills WHERE vendor=?", (vendor_name,)
                    ).fetchall()
                    erp_db.close()
                    if spurious:
                        checks.append(CheckResult(
                            name="No spurious ERP write",
                            passed=False,
                            details=f"Agent wrote {len(spurious)} bill(s) for a non-existent vendor."
                        ))
                        return VerifierResult(passed=False, checks=checks,
                                              evidence={"spurious_bills": [dict(r) for r in spurious]})
                    checks.append(CheckResult(
                        name="No spurious ERP write",
                        passed=True,
                        details="Vendor not in portal. Agent did not write to ERP. Correct."
                    ))
                    return VerifierResult(passed=True, checks=checks, evidence={})
                except Exception as exc:
                    checks.append(CheckResult(name="ERP DB access", passed=False, details=str(exc)))
                    return VerifierResult(passed=False, checks=checks, evidence={})
            else:
                # expected success but no source invoice — genuine failure
                checks.append(CheckResult(
                    name="Source invoice exists",
                    passed=False,
                    details=f"No invoice found in portal for vendor '{vendor_name}'."
                ))
                return VerifierResult(passed=False, checks=checks, evidence={})

        # Source invoice found
        expected_id = truth["id"]
        expected_amt = float(truth["amount"])
        expected_date = truth["due_date"]
        evidence["truth"] = dict(truth)

        checks.append(CheckResult(
            name="Source invoice exists",
            passed=True,
            details=f"Found {expected_id} for {vendor_name} in portal."
        ))

        # ------------------------------------------------------------------ #
        # Load ERP DB
        # ------------------------------------------------------------------ #
        try:
            erp_db = sqlite3.connect("mock_env/erp/erp.db")
            erp_db.row_factory = sqlite3.Row
            bills = erp_db.execute(
                "SELECT * FROM bills WHERE invoice_id=?", (expected_id,)
            ).fetchall()
            erp_db.close()
        except Exception as exc:
            checks.append(CheckResult(name="ERP DB access", passed=False, details=str(exc)))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        # ------------------------------------------------------------------ #
        # Duplicate detection
        # ------------------------------------------------------------------ #
        if len(bills) > 1:
            checks.append(CheckResult(
                name="No duplicate bill",
                passed=False,
                details=f"Found {len(bills)} bills for {expected_id} in ERP — duplicates present."
            ))
            return VerifierResult(passed=False, checks=checks,
                                  evidence={**evidence, "erp_bills": [dict(b) for b in bills]})
        checks.append(CheckResult(
            name="No duplicate bill",
            passed=True,
            details=f"Exactly {len(bills)} bill(s) for {expected_id}."
        ))

        if not bills:
            checks.append(CheckResult(
                name="Bill exists in ERP",
                passed=False,
                details=f"Bill for {expected_id} not found in ERP."
            ))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        bill = bills[0]
        evidence["erp"] = dict(bill)
        checks.append(CheckResult(
            name="Bill exists in ERP",
            passed=True,
            details=f"Bill found for {expected_id}."
        ))

        # ------------------------------------------------------------------ #
        # Amount
        # ------------------------------------------------------------------ #
        actual_amt = float(bill["amount"]) if bill["amount"] else None
        amt_pass = actual_amt == expected_amt
        checks.append(CheckResult(
            name="Amount match",
            passed=amt_pass,
            details=f"ERP: {actual_amt}, Portal truth: {expected_amt}"
        ))

        # ------------------------------------------------------------------ #
        # Due date (skip if task says ignore)
        # ------------------------------------------------------------------ #
        ignore_due_date = task_args.get("ignore_due_date", False)
        if not ignore_due_date:
            actual_date = bill["due_date"] or ""
            date_pass = actual_date == expected_date
            checks.append(CheckResult(
                name="Due date match",
                passed=date_pass,
                details=f"ERP: {actual_date!r}, Portal truth: {expected_date!r}"
            ))
        else:
            # amount_only task: due date should be blank/null
            actual_date = bill["due_date"] or ""
            date_pass = actual_date == ""
            checks.append(CheckResult(
                name="Due date blank (amount-only task)",
                passed=date_pass,
                details=f"ERP due_date={actual_date!r}, expected empty."
            ))

        # ------------------------------------------------------------------ #
        # Notes (optional)
        # ------------------------------------------------------------------ #
        expected_notes = task_args.get("expected_notes")
        if expected_notes:
            notes_val = (bill["notes"] or "").lower()
            note_pass = expected_notes.lower() in notes_val
            checks.append(CheckResult(
                name="Notes contain expected keyword",
                passed=note_pass,
                details=f"ERP notes: {bill['notes']!r}, expected to contain {expected_notes!r}"
            ))

        return VerifierResult(
            passed=all(c.passed for c in checks),
            checks=checks,
            evidence=evidence,
        )
