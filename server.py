import asyncio
import copy
import datetime
import json
import logging
import os
import time
import uuid

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from agent.events import EventBus
from agent.llm import LLMChainManager, get_chain_manager, run_preflight, set_chain_manager
from agent.loop import run_loop

logger = logging.getLogger("server")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("ui", exist_ok=True)
os.makedirs("runs", exist_ok=True)
os.makedirs("demo_runs", exist_ok=True)

app.mount("/ui", StaticFiles(directory="ui"), name="ui")
app.mount("/runs", StaticFiles(directory="runs"), name="runs")
app.mount("/demo_runs", StaticFiles(directory="demo_runs"), name="demo_runs")

runs = {}

# Safety & Rate limiting state
active_run_id: str | None = None
ip_run_history: dict[str, list[float]] = {}


def _reset_safety_state():
    """Helper to reset active run and IP history for testing isolation."""
    global active_run_id, ip_run_history
    active_run_id = None
    ip_run_history.clear()


class RunRequest(BaseModel):
    task: str
    chaos: str | None = None
    auto_approve: bool = False
    vendor_name: str = "Acme Corp"
    seed_profile: str = "base"
    reset_env: bool = True
    run_token: str | None = None


class RespondRequest(BaseModel):
    response: str


class ChaosRequest(BaseModel):
    flag: str


@app.post("/run")
async def start_run(req: RunRequest, request: Request):
    global active_run_id, ip_run_history

    # 1. Check RUN_TOKEN / DEMO_MODE safety
    configured_token = os.getenv("RUN_TOKEN", "").strip()
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower()

    if configured_token:
        header_token = request.headers.get("x-run-token", "").strip()
        auth_header = request.headers.get("authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            header_token = header_token or auth_header[7:].strip()
        token_provided = (req.run_token or header_token).strip()
        if not token_provided or token_provided != configured_token:
            raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing run token")
    elif demo_mode == "replay_only":
        raise HTTPException(status_code=403, detail="Forbidden: Demo mode is replay-only")

    # 2. Rate limiting: 1 active run & 5/hour per IP
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()
    history = [t for t in ip_run_history.get(client_ip, []) if now - t < 3600]
    if len(history) >= 5:
        raise HTTPException(status_code=429, detail="Rate limit exceeded: maximum 5 runs per hour")

    if active_run_id is not None:
        raise HTTPException(
            status_code=429,
            detail="Another run is currently active. Please wait for it to complete."
        )

    run_id = str(uuid.uuid4())
    active_run_id = run_id
    history.append(now)
    ip_run_history[client_ip] = history

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
        global active_run_id
        try:
            import subprocess
            import sys
            if req.reset_env:
                subprocess.run([sys.executable, "mock_env/seed.py", req.seed_profile], check=True)

            task_args = {"verifier": "invoice_entry", "vendor_name": req.vendor_name}
            res = await run_loop(req.task, task_args=task_args, event_bus=bus, run_id=run_id)
            runs[run_id]["evidence"] = res
            await bus.emit("final", {"result": "success", "evidence": res})
        except Exception as e:
            await bus.emit("final", {"result": "error", "error": str(e)})
        finally:
            if active_run_id == run_id:
                active_run_id = None

    asyncio.create_task(agent_task())
    return {"run_id": run_id}


@app.get("/stream/{run_id}")
async def stream_run(run_id: str):
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
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
            trace_path = f"demo_runs/{run_id}/trace.jsonl"
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
                date_str = datetime.datetime.fromtimestamp(first["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                yield f"data: {json.dumps({'type': 'replay_meta', 'data': {'run_id': run_id, 'date': date_str, 'original_duration': original_duration, 'compressed_duration': original_duration / speed}})}\n\n"
            except Exception:
                pass

        last_timestamp = None
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)

            screenshot_raw = entry.get("screenshot")
            screenshot_url = f"/{screenshot_raw}" if screenshot_raw else None

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
                    "screenshot_url": screenshot_url,
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
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
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
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id].get("evidence")


@app.get("/env")
async def get_env():
    env_data = config.load_environment()
    safe_apps = []
    for app_item in env_data.get("apps", []):
        app_copy = copy.deepcopy(app_item)
        if "credentials" in app_copy and isinstance(app_copy["credentials"], dict):
            if "password" in app_copy["credentials"]:
                app_copy["credentials"]["password"] = "***REDACTED***"
        safe_apps.append(app_copy)

    preflight_res = run_preflight()
    mgr = get_chain_manager()
    chain_info = preflight_res.get("entries", [])
    preflight_ok = preflight_res.get("usable", False)

    chain_order = [f"{e.provider}/{e.model}" for e in mgr.chain] if mgr else [f"{r['provider']}/{r['model']}" for r in chain_info]

    return {
        "apps": safe_apps,
        "effective_config": {
            "max_steps": config.MAX_STEPS,
            "wall_timeout_cap": config.WALL_TIMEOUT_CAP,
            "tool_timeout_cap": config.TOOL_TIMEOUT_CAP,
            "chain_order": chain_order,
            "preflight_ok": preflight_ok,
            "chain_status": chain_info,
            "environment_file": os.getenv("ENVIRONMENT_FILE", "config/environment.local.yaml"),
            "demo_mode": os.getenv("DEMO_MODE", ""),
            "run_token_required": bool(os.getenv("RUN_TOKEN"))
        }
    }


@app.get("/")
async def root():
    return FileResponse("ui/index.html")


@app.on_event("startup")
async def startup_event():
    preflight_res = run_preflight()
    mgr = LLMChainManager(check_models_list=False)
    set_chain_manager(mgr)

    chain_desc = [f"{e.provider}/{e.model}" for e in mgr.chain] if mgr else []
    logger.info(
        f"Server startup preflight: ok={preflight_res.get('usable')}. Effective config: "
        f"max_steps={config.MAX_STEPS}, wall_cap={config.WALL_TIMEOUT_CAP}s, "
        f"tool_cap={config.TOOL_TIMEOUT_CAP}s, chain={chain_desc}"
    )
