import sqlite3
from agent.verifier import BaseVerifier, CheckResult, VerifierResult

class InvoiceEntryVerifier(BaseVerifier):
    def verify(self, task_args: dict) -> VerifierResult:
        vendor_name = task_args.get("vendor_name", "Acme Corp")
        
        portal_db = sqlite3.connect("mock_env/vendor_portal/data.db")
        portal_db.row_factory = sqlite3.Row
        truth = portal_db.execute("SELECT * FROM invoices WHERE vendor=? ORDER BY date(due_date) DESC, id DESC LIMIT 1", (vendor_name,)).fetchone()
        
        checks = []
        if not truth:
            checks.append(CheckResult(name="Truth exists", passed=True, details="No truth invoice. Assuming correct finish."))
            return VerifierResult(passed=True, checks=checks, evidence={})
            
        expected_id = truth['id']
        expected_amt = float(truth['amount'])
        expected_date = truth['due_date']
        
        erp_db = sqlite3.connect("mock_env/erp/erp.db")
        erp_db.row_factory = sqlite3.Row
        bill = erp_db.execute("SELECT * FROM bills WHERE invoice_id=?", (expected_id,)).fetchone()
        
        if not bill:
            checks.append(CheckResult(name="Exists in ERP", passed=False, details=f"Bill {expected_id} not found in ERP."))
            return VerifierResult(passed=False, checks=checks, evidence={"truth": dict(truth)})
            
        checks.append(CheckResult(name="Exists in ERP", passed=True, details=f"Bill found for {expected_id}."))
        
        ignore_due_date = task_args.get("ignore_due_date", False)
        expected_notes = task_args.get("expected_notes", None)
        
        amt_pass = float(bill['amount']) == expected_amt
        checks.append(CheckResult(name="Amount match", passed=amt_pass, details=f"ERP: {bill['amount']}, Truth: {expected_amt}"))
        
        if not ignore_due_date:
            date_pass = bill['due_date'] == expected_date
            checks.append(CheckResult(name="Date match", passed=date_pass, details=f"ERP: {bill['due_date']}, Truth: {expected_date}"))
        else:
            date_pass = not bill['due_date']
            checks.append(CheckResult(name="Date match", passed=date_pass, details=f"ERP: {bill['due_date']}, Expected: empty"))
            
        if expected_notes:
            note_pass = expected_notes.lower() in bill['notes'].lower()
            checks.append(CheckResult(name="Notes match", passed=note_pass, details=f"ERP: {bill['notes']}, Expected: {expected_notes}"))
        
        return VerifierResult(
            passed=all(c.passed for c in checks), 
            checks=checks, 
            evidence={"erp": dict(bill), "truth": dict(truth)}
        )
