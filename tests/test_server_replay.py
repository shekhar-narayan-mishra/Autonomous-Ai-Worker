import json
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

from server import app


@pytest.mark.asyncio
async def test_replay_endpoint(tmp_path):
    run_id = "test_replay_run"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    
    trace_path = run_dir / "trace.jsonl"
    
    start_ts = time.time()
    
    steps = [
        {"step": 1, "timestamp": start_ts, "thought": "t1", "action": "a1", "args": {}, "observation_summary": "o1", "latency_ms": 100},
        {"step": 2, "timestamp": start_ts + 8.0, "thought": "t2", "action": "a2", "args": {}, "observation_summary": "o2", "latency_ms": 100}
    ]
    
    with open(trace_path, "w") as f:
        f.writelines(json.dumps(s) + "\n" for s in steps)
            
    orig_exists = os.path.exists
    orig_open = open

    def mock_exists(path):
        if str(path).endswith("trace.jsonl"):
            return orig_exists(str(trace_path))
        return orig_exists(path)
        
    def mock_open(path, mode="r", **kwargs):
        if str(path).endswith("trace.jsonl"):
            return orig_open(trace_path, mode, **kwargs)
        return orig_open(path, mode, **kwargs)

    import server
    server.os.path.exists = mock_exists
    server.open = mock_open
            
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_real = time.time()
        # speed = 8, so 8s wait becomes 1s wait
        response = await ac.get(f"/replay/{run_id}?speed=8")
        assert response.status_code == 200
        
        content = b""
        async for chunk in response.aiter_bytes():
            content += chunk
            
        end_real = time.time()
        
        assert (end_real - start_real) >= 0.9 # Should take at least 1s
        
        events = [e.strip() for e in content.decode().split("\n\n") if e.strip()]
        
        assert len(events) == 4 # meta, step1, step2, final
        assert "replay_meta" in events[0]
        assert "step" in events[1]
        assert "t1" in events[1]
        assert "step" in events[2]
        assert "t2" in events[2]
        assert "final" in events[3]
        assert "is_replay" in events[3]

