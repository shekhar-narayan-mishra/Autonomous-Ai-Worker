# Eval Results (offline mode)

| Task | Mode | Chaos | Outcome | Verifier | Steps | Tokens | Latency(ms) | Human | Error |
|------|------|-------|---------|----------|-------|--------|-------------|-------|-------|
| base_entry | offline | none | success | ✓ | 11 | 770 | 11 | 0 |  |
| different_vendor | offline | none | success | ✓ | 11 | 770 | 11 | 0 |  |
| overdue_flag | offline | none | success | ✓ | 11 | 770 | 11 | 0 |  |
| reconcile_mismatch | offline | none | success | ✓ | 7 | 490 | 7 | 0 |  |
| ambiguous_duplicate | offline | none | success | ✗ | 6 | 350 | 5 | 1 |  |
| wrong_vendor | offline | none | success | ✓ | 4 | 280 | 4 | 0 |  |
| missing_invoice | offline | none | success | ✗ | 5 | 280 | 4 | 0 |  |
| amount_only | offline | none | success | ✓ | 10 | 700 | 10 | 0 |  |
| duplicate_detection | offline | none | success | ✓ | 4 | 280 | 4 | 0 |  |
| phrased_differently | offline | none | success | ✓ | 11 | 770 | 11 | 0 |  |

## Summary
- **Mode**: offline
- **Total runs**: 10
- **Overall success rate**: 10/10 (100%)
- **Verifier pass rate**: 8/10 (80%)
- **Avg steps**: 8.0
- **Avg tokens**: 546
- **Avg latency**: 8ms

> **Note**: Offline mode uses ScriptedProvider — validates agent plumbing and verifier logic.
> Results do **not** represent real LLM autonomy. Use `--mode live` for real measurements.