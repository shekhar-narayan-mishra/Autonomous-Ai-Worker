from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import uuid
import os
import yaml

from agent.loop import run_loop
from agent.events import EventBus, current_event_bus

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("ui", exist_ok=True)
os.makedirs("screenshots", exist_ok=True)
app.mount("/ui", StaticFiles(directory="ui"), name="ui")
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")

runs = {}

class RunRequest(BaseModel):
    task: str
    chaos: str | None = None
    auto_approve: bool = False
    vendor_name: str = "Acme Corp"
    
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
            subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
            
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
