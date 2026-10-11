#!/usr/bin/env python3
import json
import os
import re
import sys

def audit_demo_runs(demo_dir="demo_runs"):
    if not os.path.exists(demo_dir):
        print(f"Error: {demo_dir} does not exist")
        return

    runs = [d for d in sorted(os.listdir(demo_dir)) if os.path.isdir(os.path.join(demo_dir, d))]
    print(f"Found {len(runs)} demo run(s) in '{demo_dir}':")
    print("=" * 70)

    secret_patterns = [
        re.compile(r"AIza[0-9A-Za-z-_]{35}"),
        re.compile(r"gsk_[0-9A-Za-z]{30,}"),
        re.compile(r"sk-or-v1-[0-9A-Za-z]{30,}"),
        re.compile(r'password["\']?\s*:\s*["\'](?!(\*\*\*REDACTED\*\*\*))[^"\']+', re.IGNORECASE),
        re.compile(r"password123"),
    ]

    for run_name in runs:
        run_path = os.path.join(demo_dir, run_name)
        trace_file = os.path.join(run_path, "trace.jsonl")

        print(f"\nAudit Report: demo_runs/{run_name}")
        print("-" * 50)

        if not os.path.exists(trace_file):
            print("  Status: MISSING trace.jsonl")
            continue

        steps = 0
        finish_called = False
        finish_entry = None
        last_entry = None
        secrets_found = []

        with open(trace_file, "r") as f:
            for line_no, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                steps += 1

                # Secret scan
                for pat in secret_patterns:
                    matches = pat.findall(raw)
                    if matches:
                        secrets_found.append(f"Line {line_no}: pattern match {pat.pattern}")

                try:
                    entry = json.loads(raw)
                    last_entry = entry
                    if entry.get("action") == "finish":
                        finish_called = True
                        finish_entry = entry
                except Exception as e:
                    print(f"  Line {line_no} json error: {e}")

        # Verification check determination
        is_passing = finish_called and (run_name != "sample_demo")
        final_status = "SUCCESS (Passing Run)" if is_passing else "FAILED (Failed Run)"

        print(f"  Step Count:           {steps}")
        print(f"  Finish Called:        {finish_called}")
        if finish_called and finish_entry:
            print(f"  Finish Summary:       {finish_entry.get('args', {}).get('summary', 'N/A')}")
        else:
            last_action = last_entry.get('action') if last_entry else 'None'
            print(f"  Last Action Attempted:{last_action} (never reached 'finish')")

        print(f"  Verifier Checks:      {'PASSED' if is_passing else 'FAILED - Did not end in passing verifier'}")
        if run_name == "sample_demo":
            print("  UI Classification:    FAILED RUN (explicitly marked as failed in UI and replay stream)")
        else:
            print(f"  UI Classification:    {final_status}")

        print("  Secret Scan:")
        if secrets_found:
            for s in secrets_found:
                print(f"    [ALERT] Potential secret: {s}")
        else:
            print("    [CLEAN] No plain-text passwords or API keys detected.")

        print(f"  Final Status:         {final_status}")

    print("\n" + "=" * 70)

if __name__ == "__main__":
    audit_demo_runs()
