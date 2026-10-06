# Eval Results

**Model:** llama-3.1-8b-instant

| Task ID | Success Rate | Chaos Recovery | Verifier Pass | Avg Steps | Avg Retries | Avg Tokens | Avg Latency (ms) | Unexp Q's | 429s |
|---------|--------------|----------------|---------------|-----------|-------------|------------|-----------------|-----------|------|
| base_entry | 0% | 0% | 0% | 2.3 | 0.0 | 0 | 0 | 0 | 0 |
| different_vendor | 0% | 0% | 0% | 1.0 | 0.0 | 0 | 0 | 0 | 0 |
| overdue_flag | 0% | 0% | 0% | 1.0 | 0.0 | 0 | 0 | 0 | 0 |
| reconcile_mismatch | 0% | 0% | 0% | 0.8 | 0.0 | 0 | 0 | 0 | 0 |
| ambiguous_duplicate | 0% | 0% | 0% | 1.5 | 0.0 | 0 | 0 | 0 | 0 |
| wrong_vendor | 0% | 0% | 0% | 0.7 | 0.0 | 0 | 0 | 0 | 0 |
| missing_invoice | 0% | 0% | 0% | 1.2 | 0.0 | 0 | 0 | 0 | 0 |
| amount_only | 0% | 0% | 0% | 2.0 | 0.0 | 0 | 0 | 0 | 0 |
| duplicate_detection | 0% | 0% | 0% | 2.0 | 0.0 | 0 | 0 | 0 | 0 |
| phrased_differently | 0% | 0% | 0% | 0.0 | 0.0 | 0 | 0 | 0 | 0 |

## Failures
- **base_entry** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **base_entry** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **base_entry** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **base_entry** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **base_entry** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **base_entry** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **different_vendor** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **overdue_flag** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **overdue_flag** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **overdue_flag** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=True, Verifier=False
- **overdue_flag** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **overdue_flag** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **overdue_flag** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **reconcile_mismatch** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 1, Chaos=False): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 2, Chaos=False): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 3, Chaos=False): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 4, Chaos=True): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 5, Chaos=True): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **ambiguous_duplicate** (Run 6, Chaos=True): Expected must_ask_clarification, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 1, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 2, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 3, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 4, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 5, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **wrong_vendor** (Run 6, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 1, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 2, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 3, Chaos=False): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 4, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 5, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **missing_invoice** (Run 6, Chaos=True): Expected must_report_not_found, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **amount_only** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **duplicate_detection** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **duplicate_detection** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=True, Verifier=False
- **duplicate_detection** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **duplicate_detection** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **duplicate_detection** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **duplicate_detection** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 1, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 2, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 3, Chaos=False): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 4, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 5, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
- **phrased_differently** (Run 6, Chaos=True): Expected success, got failed. Details: SysExit=False, Verifier=False
