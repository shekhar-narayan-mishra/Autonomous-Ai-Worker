import builtins
from unittest.mock import MagicMock, patch

import pytest

from agent.llm import LLMQuotaExhaustedError
from eval.live import generate_md, main

original_open = builtins.open
mock_file_obj = MagicMock()
mock_file_obj.__enter__.return_value = mock_file_obj

def mock_open_wrapper(*args, **kwargs):
    filename = args[0]
    if "results_live.md" in str(filename) or "results_live.json" in str(filename):
        return mock_file_obj
    return original_open(*args, **kwargs)

def test_generate_md_infra_exclusion(tmp_path):
    results = {
        "base/1": {"status": "SUCCESS", "verifier_pass": True, "steps": 1},
        "base/2": {"status": "FAILED_VERIFICATION", "verifier_pass": False},
        "base/3": {"status": "infra_error: some error", "verifier_pass": False},
        "base/4": {"status": "not_run (quota)", "verifier_pass": False}
    }
    with patch("builtins.open", side_effect=mock_open_wrapper):
        generate_md(results)
        written = "".join([call.args[0] for call in mock_file_obj.write.mock_calls])
        assert "**Success Rate:** 50.0% (1/2) [Excluded 1 infra errors]" in written
        assert "base | 4 | not_run (quota) | - | - | -" in written
        assert "base | 3 | infra_error: some error | No | 0" in written

@pytest.mark.asyncio
async def test_budget_guard_and_not_run(tmp_path):
    calls = 0
    async def mock_run_loop(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"status": "SUCCESS", "passed": True}
        else:
            raise LLMQuotaExhaustedError("out of quota")

    with patch("eval.live.run_loop", side_effect=mock_run_loop), \
         patch("sys.argv", ["live.py", "--tasks", "base, duplicate_detection, missing_invoice", "--go", "--runs", "1"]), \
         patch("os.path.exists", return_value=False), \
         patch("builtins.open", side_effect=mock_open_wrapper), \
         patch("eval.live.parse_trace", return_value={"steps":1, "retries":0, "llm_calls":1, "tokens":0, "providers":["dummy"], "status_429s":0, "switches":0, "llm_wait_total":0}), \
         patch("json.dump") as mock_dump:
        
        await main()
        last_dump = mock_dump.mock_calls[-1].args[0]
        assert last_dump["base/1"]["status"] == "SUCCESS"
        assert last_dump["duplicate_detection/1"]["status"] == "FAILED_QUOTA"
        assert last_dump["missing_invoice/1"]["status"] == "not_run (quota)"

@pytest.mark.asyncio
async def test_resume(tmp_path):
    existing_results = {
        "base/1": {"status": "SUCCESS", "verifier_pass": True}
    }
    
    async def mock_run_loop(*args, **kwargs):
        return {"status": "SUCCESS", "passed": True}

    with patch("eval.live.run_loop", side_effect=mock_run_loop) as mock_rl, \
         patch("sys.argv", ["live.py", "--tasks", "base, duplicate_detection", "--go"]), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=mock_open_wrapper), \
         patch("json.load", return_value=existing_results.copy()), \
         patch("eval.live.parse_trace", return_value={"steps":1, "retries":0, "llm_calls":1, "tokens":0, "providers":["dummy"], "status_429s":0, "switches":0, "llm_wait_total":0}), \
         patch("json.dump") as mock_dump:
        
        await main()
        assert mock_rl.call_count == 1
        last_dump = mock_dump.mock_calls[-1].args[0]
        assert last_dump["base/1"]["status"] == "SUCCESS"
        assert last_dump["duplicate_detection/1"]["status"] == "SUCCESS"
