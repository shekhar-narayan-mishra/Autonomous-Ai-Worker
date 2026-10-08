import pytest
import subprocess
import time

@pytest.fixture(scope="session", autouse=True)
def start_mock_servers():
    print("Starting mock servers...")
    vendor = subprocess.Popen(["venv/bin/python", "-m", "uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
    erp = subprocess.Popen(["venv/bin/python", "-m", "uvicorn", "mock_env.erp.main:app", "--port", "8002"])
    
    # wait a bit for them to start
    time.sleep(2)
    
    yield
    
    print("Stopping mock servers...")
    vendor.terminate()
    erp.terminate()
    vendor.wait()
    erp.wait()
