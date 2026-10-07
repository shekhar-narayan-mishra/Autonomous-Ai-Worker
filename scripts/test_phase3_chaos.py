import json
import os
import subprocess
import time


def test_chaos():
    os.environ["EVAL_MODE"] = "1"
    os.environ["AUTO_APPROVE"] = "true"
    
    # 1. Base task
    # 2-7. Chaos tasks
    flags = [
        None, # Base
        "slow_load",
        "random_popup_modal",
        "expired_session",
        "validation_error_on_first_submit",
        "missing_field",
        "flaky_500"
    ]
    
    base_task = "Find the latest invoice from Acme Corp, extract the amount and due date, and enter it into the ERP."
    base_args = '{"verifier": "invoice_entry", "vendor_name": "Acme Corp"}'
    
    for flag in flags:
        name = flag if flag else "Base run (no chaos)"
        print(f"\n{'='*50}\nTESTING: {name}\n{'='*50}")
        subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
        
        if flag:
            with open("chaos_state.json", "w") as f:
                json.dump({flag: True}, f)
        else:
            if os.path.exists("chaos_state.json"):
                os.remove("chaos_state.json")
                
        vp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
        erp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.erp.main:app", "--port", "8002"])
        
        time.sleep(2)
        subprocess.run(["venv/bin/python", "-m", "agent.run", base_task, base_args])
        
        vp_proc.terminate()
        erp_proc.terminate()
        vp_proc.wait()
        erp_proc.wait()

    # 8. Not Found Case
    print(f"\n{'='*50}\nTESTING: Not found case\n{'='*50}")
    if os.path.exists("chaos_state.json"):
        os.remove("chaos_state.json")
    subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
    vp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
    erp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.erp.main:app", "--port", "8002"])
    time.sleep(2)
    
    not_found_task = "Find the latest invoice from UnknownCorp, extract the amount and due date, and enter it into the ERP."
    not_found_args = '{"verifier": "invoice_entry", "vendor_name": "UnknownCorp"}'
    subprocess.run(["venv/bin/python", "-m", "agent.run", not_found_task, not_found_args])
    vp_proc.terminate()
    erp_proc.terminate()
    vp_proc.wait()
    erp_proc.wait()

    # 9. Ambiguity case
    print(f"\n{'='*50}\nTESTING: Ambiguity case\n{'='*50}")
    subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
    vp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
    erp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.erp.main:app", "--port", "8002"])
    time.sleep(2)
    
    amb_task = "Find the latest invoice from Globex, extract the amount and due date, and enter it into the ERP."
    amb_args = json.dumps({
        "verifier": "invoice_entry",
        "vendor_name": "Globex",
        "expected_questions": {"which": "INV-AMB-1 is the correct one.", "multiple": "INV-AMB-1 is the correct one.", "duplicate": "INV-AMB-1 is the correct one.", "two": "INV-AMB-1 is the correct one.", "globex": "INV-AMB-1"}
    })
    subprocess.run(["venv/bin/python", "-m", "agent.run", amb_task, amb_args])
    vp_proc.terminate()
    erp_proc.terminate()
    vp_proc.wait()
    erp_proc.wait()

if __name__ == "__main__":
    test_chaos()
