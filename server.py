import asyncio
import json
import os
import uuid

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.events import EventBus
from agent.loop import run_loop

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("ui", exist_ok=True)
os.makedirs("runs", exist_ok=True)
app.mount("/ui", StaticFiles(directory="ui"), name="ui")
app.mount("/runs", StaticFiles(directory="runs"), name="runs")

runs = {}

class RunRequest(BaseModel):
    task: str
    chaos: str | None = None
    auto_approve: bool = False
    vendor_name: str = "Acme Corp"
    seed_profile: str = "base"
    reset_env: bool = True
    
class RespondRequest(BaseModel):
    response: str
    
class ChaosRequest(BaseModel):
    flag: str

@app.post("/run")
async def start_run(req: RunRequest):
    run_id = str(uuid.uuid4())
    bus = EventBus()
    runs[run_id] = {"bus": bus, "task": req.task, "evidence": None}
    
    if req.chaos:
        with open("chaos_state.json", "w") as f:
            json.dump({req.chaos: True}, f)
    else:
        if os.path.exists("chaos_state.json"):
            os.remove("chaos_state.json")
            
    os.environ["AUTO_APPROVE"] = str(req.auto_approve).lower()
    os.environ["EVAL_MODE"] = "0"
    
    async def agent_task():
        try:
            import subprocess
            if req.reset_env:
                subprocess.run(["venv/bin/python", "mock_env/seed.py", req.seed_profile], check=True)
            
            task_args = {"verifier": "invoice_entry", "vendor_name": req.vendor_name}
            res = await run_loop(req.task, task_args=task_args, event_bus=bus, run_id=run_id)
            runs[run_id]["evidence"] = res
            await bus.emit("final", {"result": "success", "evidence": res})
        except Exception as e:
            await bus.emit("final", {"result": "error", "error": str(e)})
            
    asyncio.create_task(agent_task())
    return {"run_id": run_id}

@app.get("/stream/{run_id}")
async def stream_run(run_id: str):
    bus = runs[run_id]["bus"]
    
    async def event_generator():
        while True:
            event = await bus.queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] == "final":
                break
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/replay/{run_id}")
async def replay_run(run_id: str, speed: float = 4.0):
    async def event_generator():
        trace_path = f"runs/{run_id}/trace.jsonl"
        if not os.path.exists(trace_path):
            yield f"data: {json.dumps({'type': 'final', 'data': {'result': 'error', 'error': 'trace not found'}})}\n\n"
            return
            
        with open(trace_path, "r") as f:
            lines = f.readlines()
            
        if lines:
            try:
                first = json.loads(lines[0])
                last = json.loads(lines[-1])
                original_duration = last["timestamp"] - first["timestamp"]
                import datetime
                date_str = datetime.datetime.fromtimestamp(first["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                yield f"data: {json.dumps({'type': 'replay_meta', 'data': {'run_id': run_id, 'date': date_str, 'original_duration': original_duration, 'compressed_duration': original_duration / speed}})}\n\n"
            except Exception:
                pass
            
        last_timestamp = None
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            
            event = {
                "type": "step",
                "data": {
                    "step": entry.get("step"),
                    "thought": entry.get("thought"),
                    "action": entry.get("action"),
                    "args": entry.get("args"),
                    "observation": entry.get("observation_summary"),
                    "latency_ms": entry.get("llm_inference_ms", entry.get("latency_ms", 0)),
                    "wait_ms": entry.get("llm_wait_ms", 0),
                    "tool_ms": entry.get("tool_ms", 0),
                    "screenshot_url": f"/{entry['screenshot']}" if entry.get("screenshot") else None,
                    "retry_reason": None,
                    "is_replay": True,
                    "speed": speed
                }
            }
            
            if last_timestamp is not None:
                delay = entry["timestamp"] - last_timestamp
                if delay > 0:
                    await asyncio.sleep(delay / speed)
            
            yield f"data: {json.dumps(event)}\n\n"
            last_timestamp = entry["timestamp"]
            
        yield f"data: {json.dumps({'type': 'final', 'data': {'result': 'success', 'is_replay': True}})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/respond/{run_id}")
async def respond(run_id: str, req: RespondRequest):
    bus = runs[run_id]["bus"]
    bus.respond(req.response)
    return {"status": "ok"}
    
@app.post("/chaos")
async def set_chaos(req: ChaosRequest):
    if req.flag:
        with open("chaos_state.json", "w") as f:
            json.dump({req.flag: True}, f)
    else:
        if os.path.exists("chaos_state.json"):
            os.remove("chaos_state.json")
    return {"status": "ok"}
    
@app.get("/runs/{run_id}/evidence")
async def get_evidence(run_id: str):
    return runs[run_id].get("evidence")
    
@app.get("/env")
async def get_env():
    with open("config/environment.yaml", "r") as f:
        return yaml.safe_load(f)

@app.get("/")
async def root():
    return FileResponse("ui/index.html")

@app.on_event("startup")
async def startup_event():
    async def warmup():
        import time
        start = time.time()
        from agent.llm import LLMChainManager, set_chain_manager
        mgr = LLMChainManager(check_models_list=True)
        set_chain_manager(mgr)
        latency = time.time() - start
        print(f"llm_chain_ready event emitted. Init time: {latency:.2f}s")
    asyncio.create_task(warmup())
