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
from agent.env_manager import get_active_profile_name
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
            task_args = {
                "verifier": "invoice_entry",
                "vendor_name": req.vendor_name,
                "reset_env": req.reset_env,
                "seed_profile": req.seed_profile,
            }
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
        has_finish = False
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("action") == "finish":
                has_finish = True

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

        is_passing = has_finish and (run_id != "sample_demo")
        if is_passing:
            yield f"data: {json.dumps({'type': 'final', 'data': {'result': 'success', 'is_replay': True}})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'final', 'data': {'result': 'failed', 'is_replay': True, 'error': 'Run failed: did not end in a passing verifier'}})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "demo_mode": os.getenv("DEMO_MODE", "").strip().lower() == "replay_only",
        "timestamp": time.time()
    }


def get_git_sha() -> str:
    for env_var in ("GIT_SHA", "RENDER_GIT_COMMIT", "COMMIT_SHA"):
        val = os.getenv(env_var, "").strip()
        if val:
            return val
    try:
        import subprocess
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_provider_preflight_info() -> dict:
    providers = {}
    # gemini
    gemini_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    gemini_import = False
    gemini_client = False
    try:
        from google import genai
        gemini_import = True
        if gemini_key:
            try:
                _ = genai.Client(api_key=os.getenv("GEMINI_API_KEY", "").strip())
                gemini_client = True
            except Exception:
                pass
    except Exception:
        pass
    providers["gemini"] = {
        "import_ok": gemini_import,
        "client_ok": gemini_client,
        "key_present": gemini_key
    }

    # groq
    groq_key = bool(os.getenv("GROQ_API_KEY", "").strip())
    groq_import = False
    groq_client = False
    try:
        from groq import AsyncGroq
        groq_import = True
        if groq_key:
            try:
                _ = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", "").strip())
                groq_client = True
            except Exception:
                pass
    except Exception:
        pass
    providers["groq"] = {
        "import_ok": groq_import,
        "client_ok": groq_client,
        "key_present": groq_key
    }

    # openrouter
    openrouter_key = bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    openrouter_import = False
    openrouter_client = False
    try:
        import httpx
        openrouter_import = True
        if openrouter_key:
            try:
                _ = httpx.AsyncClient(timeout=60.0)
                openrouter_client = True
            except Exception:
                pass
    except Exception:
        pass
    providers["openrouter"] = {
        "import_ok": openrouter_import,
        "client_ok": openrouter_client,
        "key_present": openrouter_key
    }

    return providers


@app.get("/version")
async def get_version():
    import importlib.metadata
    import platform

    def pkg_ver(name: str):
        try:
            return importlib.metadata.version(name)
        except Exception:
            return None

    demo_mode = os.getenv("DEMO_MODE", "").strip().lower()
    return {
        "git_sha": get_git_sha(),
        "active_profile": get_active_profile_name(),
        "python_version": platform.python_version(),
        "packages": {
            "google_genai": pkg_ver("google-genai"),
            "groq": pkg_ver("groq"),
            "httpx": pkg_ver("httpx"),
            "playwright": pkg_ver("playwright"),
        },
        "demo_mode": demo_mode,
        "environment_profile": os.getenv("ENVIRONMENT_FILE", "config/environment.local.yaml"),
        "providers": get_provider_preflight_info()
    }


@app.get("/demo_runs")
async def list_demo_runs():
    results = []
    if os.path.exists("demo_runs"):
        for name in sorted(os.listdir("demo_runs")):
            run_dir = os.path.join("demo_runs", name)
            if not os.path.isdir(run_dir):
                continue
            trace_path = os.path.join(run_dir, "trace.jsonl")
            if not os.path.exists(trace_path):
                continue

            steps = 0
            finish_called = False
            with open(trace_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    steps += 1
                    try:
                        item = json.loads(line)
                        if item.get("action") == "finish":
                            finish_called = True
                    except Exception:
                        pass

            is_passing = finish_called and (name != "sample_demo")
            status = "success" if is_passing else "failed"
            label = "Passing run" if is_passing else "Failed run"

            results.append({
                "id": name,
                "name": name,
                "steps": steps,
                "finish_called": finish_called,
                "is_passing": is_passing,
                "status": status,
                "label": label,
                "description": f"Recorded run ({steps} steps, {'completed' if finish_called else 'incomplete / verifier not passed'})"
            })
    return results


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
            for k in list(app_copy["credentials"].keys()):
                if any(s in k.lower() for s in ["password", "pwd", "secret", "token", "key"]):
                    app_copy["credentials"][k] = "***REDACTED***"
        safe_apps.append(app_copy)

    active_profile = get_active_profile_name()
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower()
    if demo_mode == "replay_only":
        return {
            "active_profile": active_profile,
            "apps": safe_apps,
            "effective_config": {
                "active_profile": active_profile,
                "max_steps": config.MAX_STEPS,
                "wall_timeout_cap": config.WALL_TIMEOUT_CAP,
                "tool_timeout_cap": config.TOOL_TIMEOUT_CAP,
                "chain_order": [],
                "preflight_ok": None,
                "chain_status": [],
                "environment_file": os.getenv("ENVIRONMENT_FILE", "config/environment.local.yaml"),
                "demo_mode": "replay_only",
                "run_token_required": bool(os.getenv("RUN_TOKEN"))
            }
        }

    preflight_res = run_preflight()
    mgr = get_chain_manager()
    chain_info = preflight_res.get("entries", [])
    preflight_ok = preflight_res.get("usable", False)

    chain_order = [f"{e.provider}/{e.model}" for e in mgr.chain] if mgr else [f"{r['provider']}/{r['model']}" for r in chain_info]

    return {
        "active_profile": active_profile,
        "apps": safe_apps,
        "effective_config": {
            "active_profile": active_profile,
            "max_steps": config.MAX_STEPS,
            "wall_timeout_cap": config.WALL_TIMEOUT_CAP,
            "tool_timeout_cap": config.TOOL_TIMEOUT_CAP,
            "chain_order": chain_order,
            "preflight_ok": preflight_ok,
            "chain_status": chain_info,
            "environment_file": os.getenv("ENVIRONMENT_FILE", "config/environment.local.yaml"),
            "demo_mode": demo_mode,
            "run_token_required": bool(os.getenv("RUN_TOKEN"))
        }
    }


@app.get("/")
async def root():
    return FileResponse("ui/index.html")


@app.on_event("startup")
async def startup_event():
    demo_mode = os.getenv("DEMO_MODE", "").strip().lower()
    if demo_mode == "replay_only":
        logger.info("Server startup in DEMO_MODE=replay_only: skipping LLM preflight and chain manager.")
        return

    preflight_res = run_preflight()
    mgr = LLMChainManager(check_models_list=False)
    set_chain_manager(mgr)

    chain_desc = [f"{e.provider}/{e.model}" for e in mgr.chain] if mgr else []
    logger.info(
        f"Server startup preflight: ok={preflight_res.get('usable')}. Effective config: "
        f"max_steps={config.MAX_STEPS}, wall_cap={config.WALL_TIMEOUT_CAP}s, "
        f"tool_cap={config.TOOL_TIMEOUT_CAP}s, chain={chain_desc}"
    )
