"""
InvoiceEntryVerifier — independent cross-app verification over HTTP.

Verifies that:
1. For normal entry tasks:   invoice exists in vendor portal (API), correct one selected,
                             ERP bill exists (API), amount/date/vendor match, no duplicate.
2. For not-found tasks:      no unintended ERP writes occurred.
3. For duplicate detection:  no second bill was added.
4. For ambiguity tasks:      agent asked human (checked by the loop, not here).
"""
from __future__ import annotations

import httpx
from agent.env_manager import get_app_base_url
from agent.verifier import BaseVerifier, CheckResult, VerifierResult


class InvoiceEntryVerifier(BaseVerifier):
    def verify(self, task_args: dict) -> VerifierResult:
        vendor_name = task_args.get("vendor_name", "Acme Corp")
        expected_outcome = task_args.get("expected_outcome", "success")

        portal_url = get_app_base_url("Vendor Portal")
        erp_url = get_app_base_url("Internal ERP")

        bills_before = task_args.get("initial_erp_bills", [])
        evidence: dict = {
            "portal_url": portal_url,
            "erp_url": erp_url,
            "bills_before": bills_before,
        }
        checks: list[CheckResult] = []

        # ------------------------------------------------------------------ #
        # Load ground truth from Vendor Portal via HTTP API
        # ------------------------------------------------------------------ #
        invoices = []
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{portal_url}/api/invoices")
                if r.status_code != 200:
                    checks.append(CheckResult(
                        name="Portal API access",
                        passed=False,
                        details=f"Portal API at {portal_url}/api/invoices returned HTTP {r.status_code}: {r.text}"
                    ))
                    evidence["bills_after"] = []
                    evidence["bill_created_in_run"] = False
                    return VerifierResult(passed=False, checks=checks, evidence=evidence)
                invoices = r.json()
        except Exception as exc:
            checks.append(CheckResult(
                name="Portal API access",
                passed=False,
                details=f"Could not connect to Portal API at {portal_url}/api/invoices: {exc}"
            ))
            evidence["bills_after"] = []
            evidence["bill_created_in_run"] = False
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        # Sort matching invoices by date(due_date) DESC, id DESC
        vendor_invoices = [inv for inv in invoices if inv.get("vendor") == vendor_name]
        vendor_invoices.sort(key=lambda x: (x.get("due_date") or "", str(x.get("id") or "")), reverse=True)
        truth = vendor_invoices[0] if vendor_invoices else None

        # ------------------------------------------------------------------ #
        # Load ERP bills via HTTP API
        # ------------------------------------------------------------------ #
        all_bills = []
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{erp_url}/api/bills")
                if r.status_code != 200:
                    checks.append(CheckResult(
                        name="ERP API access",
                        passed=False,
                        details=f"ERP API at {erp_url}/api/bills returned HTTP {r.status_code}: {r.text}"
                    ))
                    evidence["bills_after"] = []
                    evidence["bill_created_in_run"] = False
                    return VerifierResult(passed=False, checks=checks, evidence=evidence)
                all_bills = r.json()
        except Exception as exc:
            checks.append(CheckResult(
                name="ERP API access",
                passed=False,
                details=f"Could not connect to ERP API at {erp_url}/api/bills: {exc}"
            ))
            evidence["bills_after"] = []
            evidence["bill_created_in_run"] = False
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        evidence["bills_after"] = all_bills

        # ------------------------------------------------------------------ #
        # NOT_FOUND / WRONG_VENDOR case
        # ------------------------------------------------------------------ #
        if not truth:
            if expected_outcome == "must_report_not_found":
                spurious = [b for b in all_bills if b.get("vendor") == vendor_name]
                if spurious:
                    checks.append(CheckResult(
                        name="No spurious ERP write",
                        passed=False,
                        details=f"Agent wrote {len(spurious)} bill(s) for a non-existent vendor to ERP at {erp_url}."
                    ))
                    evidence["spurious_bills"] = spurious
                    evidence["bill_created_in_run"] = True
                    return VerifierResult(passed=False, checks=checks, evidence=evidence)
                checks.append(CheckResult(
                    name="No spurious ERP write",
                    passed=True,
                    details=f"Vendor not in portal ({portal_url}). Agent did not write to ERP at {erp_url}. Correct."
                ))
                evidence["bill_created_in_run"] = False
                return VerifierResult(passed=True, checks=checks, evidence=evidence)
            else:
                checks.append(CheckResult(
                    name="Source invoice exists",
                    passed=False,
                    details=f"No invoice found in portal at {portal_url}/api/invoices for vendor '{vendor_name}'."
                ))
                evidence["bill_created_in_run"] = False
                return VerifierResult(passed=False, checks=checks, evidence=evidence)

        # Source invoice found
        expected_id = truth["id"]
        expected_amt = float(truth["amount"])
        expected_date = truth["due_date"]
        evidence["truth"] = dict(truth)

        checks.append(CheckResult(
            name="Source invoice exists",
            passed=True,
            details=f"Found {expected_id} for {vendor_name} in portal ({portal_url})."
        ))

        # ------------------------------------------------------------------ #
        # Evaluate ERP bills for matching invoice
        # ------------------------------------------------------------------ #
        matching_bills = [b for b in all_bills if b.get("invoice_id") == expected_id]
        matching_before = [b for b in bills_before if b.get("invoice_id") == expected_id]
        bill_created_in_run = len(matching_bills) > len(matching_before)
        evidence["matching_bills"] = matching_bills
        evidence["bill_created_in_run"] = bill_created_in_run

        # Duplicate detection
        if len(matching_bills) > 1:
            checks.append(CheckResult(
                name="No duplicate bill",
                passed=False,
                details=f"Found {len(matching_bills)} bills for {expected_id} in ERP at {erp_url} — duplicates present."
            ))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        checks.append(CheckResult(
            name="No duplicate bill",
            passed=True,
            details=f"Exactly {len(matching_bills)} bill(s) for {expected_id} in ERP at {erp_url}."
        ))

        if not matching_bills:
            checks.append(CheckResult(
                name="Bill exists in ERP",
                passed=False,
                details=f"Bill for {expected_id} not found in ERP at {erp_url}."
            ))
            return VerifierResult(passed=False, checks=checks, evidence=evidence)

        bill = matching_bills[0]
        evidence["erp"] = dict(bill)
        checks.append(CheckResult(
            name="Bill exists in ERP",
            passed=True,
            details=f"Bill found for {expected_id} in ERP at {erp_url}."
        ))

        # Vendor match
        actual_vendor = bill.get("vendor") or ""
        vendor_pass = actual_vendor.strip().lower() == vendor_name.strip().lower()
        checks.append(CheckResult(
            name="Vendor match",
            passed=vendor_pass,
            details=f"ERP at {erp_url}: {actual_vendor!r}, Portal truth: {vendor_name!r}"
        ))

        # Amount match
        actual_amt = float(bill["amount"]) if bill.get("amount") is not None else None
        amt_pass = actual_amt == expected_amt
        checks.append(CheckResult(
            name="Amount match",
            passed=amt_pass,
            details=f"ERP at {erp_url}: {actual_amt}, Portal truth: {expected_amt}"
        ))

        # Due date match
        ignore_due_date = task_args.get("ignore_due_date", False)
        if not ignore_due_date:
            actual_date = bill.get("due_date") or ""
            date_pass = actual_date == expected_date
            checks.append(CheckResult(
                name="Due date match",
                passed=date_pass,
                details=f"ERP at {erp_url}: {actual_date!r}, Portal truth: {expected_date!r}"
            ))
        else:
            actual_date = bill.get("due_date") or ""
            date_pass = actual_date == ""
            checks.append(CheckResult(
                name="Due date blank (amount-only task)",
                passed=date_pass,
                details=f"ERP at {erp_url} due_date={actual_date!r}, expected empty."
            ))

        # Notes match
        expected_notes = task_args.get("expected_notes")
        if expected_notes:
            notes_val = (bill.get("notes") or "").lower()
            note_pass = expected_notes.lower() in notes_val
            checks.append(CheckResult(
                name="Notes contain expected keyword",
                passed=note_pass,
                details=f"ERP at {erp_url} notes: {bill.get('notes')!r}, expected to contain {expected_notes!r}"
            ))

        return VerifierResult(
            passed=all(c.passed for c in checks),
            checks=checks,
            evidence=evidence,
        )
