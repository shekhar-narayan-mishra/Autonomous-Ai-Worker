# Deployment Guide: Autonomous AI Task Worker

This guide explains how to deploy the Autonomous AI Task Worker system to cloud hosting environments (such as Render, Railway, Fly.io, or any Linux VPS) and answers the core architecture question regarding the mock target websites.

---

## 1. Should You Deploy the Two Mock Websites As Well?

### **Short Answer: YES, but bundle them internally.**

The entire workflow of this autonomous worker is **cross-application automation**:
1. The agent launches a headless browser (Playwright) to log into the **Vendor Portal** and locate invoice records.
2. The agent navigates to the **Internal ERP** to log in, fill accounting forms, and submit bills.
3. The deterministic verifier inspects both the Vendor Portal and the ERP database to score the run.

If the Vendor Portal and Internal ERP are not running and reachable by the agent:
> Any task triggered by an evaluator, recruiter, or user will fail immediately with `ERR_CONNECTION_REFUSED` on step 1.

---

### **How to Deploy Them (Without Extra Cost or Domains)**

You **do NOT** need to purchase domains or configure 3 separate cloud web services. 

Instead, use an **All-in-One Container Architecture**:
- **Only Port 8000 (Main UI & Server)** is exposed to the public internet for visitors.
- **Port 8001 (Vendor Portal)** and **Port 8002 (Internal ERP)** run as local background processes on `127.0.0.1` inside the same machine/container.
- Since Playwright runs *inside* that same container/host, `http://localhost:8001` and `http://localhost:8002` (as configured in `config/environment.yaml`) work instantly without changing any URL settings or managing cross-origin CORS!

```mermaid
flowchart TD
    User([Public Visitor / Browser]) -->|HTTP Port 8000| Server[FastAPI Server & Web UI]
    subgraph Container / Host Instance
        Server --> Agent[Agent Runtime / Playwright Engine]
        Agent -->|Internal: localhost:8001| VP[Vendor Portal]
        Agent -->|Internal: localhost:8002| ERP[Internal ERP]
        Verifier[Deterministic Verifier] -->|Checks DB/API| VP
        Verifier -->|Checks DB/API| ERP
    end
```

---

## 2. Deployment Options

### Option A: Docker / Docker Compose (Recommended)

A production-ready [Dockerfile](file:///Users/shekharnarayanmishra/Desktop/Autonomus%20AI/Dockerfile) and [docker-compose.yml](file:///Users/shekharnarayanmishra/Desktop/Autonomus%20AI/docker-compose.yml) are included in this repository.

#### Prerequisites
- Docker & Docker Compose installed.

#### Run with Docker Compose
```bash
# 1. Set your LLM keys in .env
cp .env.example .env
# Edit .env with your GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY

# 2. Build and launch
docker compose up -d --build

# 3. View logs
docker compose logs -f
```
The public UI will be available at `http://your-server-ip:8000`.

---

### Option B: Cloud PaaS (Render / Railway / Fly.io)

Cloud PaaS providers allow zero-devops continuous deployment directly from your GitHub repository.

#### 1. Deploying on Render (Complete Guide)

Render is one of the easiest ways to host this application because it natively supports Docker and provides automatic HTTPS, continuous deployment from GitHub, and free/starter tier options.

##### **Do you need to deploy 3 separate services on Render?**
**No.** Render will run everything inside **one single Web Service**:
- **Public Port ($PORT / 8000)**: Render routes external HTTPS traffic to the FastAPI server and UI dashboard.
- **Internal Ports (8001 & 8002)**: The Vendor Portal and Internal ERP run concurrently inside the container on `127.0.0.1`. The agent's headless Chromium browser accesses them locally at zero latency without exposing internal mock databases to the public web.

---

##### **Method A: Quick Dashboard Deployment (Recommended)**
1. **Push your code to GitHub** (make sure your repo has the `Dockerfile` and `entrypoint.sh`).
2. Log in to [Render Dashboard](https://dashboard.render.com/).
3. Click the **New +** button in the top right and select **Web Service**.
4. Choose **Build and deploy from a Git repository** and connect your `Autonomous-Ai-Worker` repo.
5. Configure the service settings:
   - **Name**: `autonomous-ai-worker` (or your preferred name)
   - **Region**: Choose the closest region (e.g., Oregon, Ohio, or Frankfurt)
   - **Runtime / Environment**: Select **Docker** (Render will automatically detect the [Dockerfile](file:///Users/shekharnarayanmishra/Desktop/Autonomus%20AI/Dockerfile))
   - **Branch**: `main`
   - **Instance Type**:
     - *Free Plan (512 MB RAM)*: Works for sequential single-run tasks with Chromium flags `--no-sandbox --disable-dev-shm-usage`.
     - *Starter Plan (1 GB RAM, $7/mo)*: Highly recommended for instant response and smooth headless Chromium multitasking.
6. Scroll down to **Environment Variables** and add your secrets:
   - `GEMINI_API_KEY`: Your Google Gemini API key
   - `GROQ_API_KEY`: *(Optional)* Your Groq key for fast failover/reasoning
   - `OPENROUTER_API_KEY`: *(Optional)* Your OpenRouter key
   - `PYTHONUNBUFFERED`: `1`
7. Click **Deploy Web Service**.
8. Render will build the container, install Playwright with Chromium, run `seed.py`, and launch all three internal servers.
9. Once the build finishes, open the provided URL (e.g. `https://autonomous-ai-worker.onrender.com`).

---

##### **Method B: Infrastructure-as-Code via Blueprint (`render.yaml`)**
This repository includes a preconfigured [render.yaml](file:///Users/shekharnarayanmishra/Desktop/Autonomus%20AI/render.yaml) file:
1. In Render Dashboard, click **New +** -> **Blueprint**.
2. Connect your `Autonomous-Ai-Worker` repository.
3. Render reads `render.yaml` and auto-fills all service configurations.
4. Input your `GEMINI_API_KEY` when prompted and click **Apply**.

---

##### **Render Free Tier Tips & Caveats**
- **Cold Starts**: Render's free tier spins down after 15 minutes of inactivity. When visiting the URL after inactivity, the first page load may take ~45–60 seconds while the container boots.
- **Database Persistence**: The mock databases (`data.db` and `erp.db`) are SQLite files. On ephemeral free instances, they reset on each deployment or restart. `seed.py` automatically initializes clean, seeded invoice data on every boot, so the agent will always have fresh data to test against!

#### 2. Deploying on Railway
1. Click **New Project** -> **Deploy from GitHub repo**.
2. Railway detects the `Dockerfile` automatically.
3. Under **Variables**, add your API keys (`GEMINI_API_KEY`, etc.).
4. Under **Settings** -> **Networking**, generate a public domain pointing to port `8000`.

#### 3. Deploying on Fly.io
```bash
# Launch application from project directory
fly launch --no-deploy

# Set secret keys
fly secrets set GEMINI_API_KEY=your_key GROQ_API_KEY=your_key

# Deploy
fly deploy
```

---

### Option C: Bare Linux VPS (Ubuntu / Debian / EC2 / DigitalOcean)

If deploying directly to a virtual machine without Docker:

```bash
# 1. Update system and install Python 3.11 + Git
sudo apt update && sudo apt install -y python3-pip python3-venv git curl

# 2. Clone repository
git clone https://github.com/your-username/Autonomous-Ai-Worker.git
cd Autonomous-Ai-Worker

# 3. Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Install Playwright browser and system OS libraries
playwright install --with-deps chromium

# 5. Initialize mock databases
python mock_env/seed.py

# 6. Configure environment variables
cp .env.example .env
nano .env

# 7. Start the stack in background using the entrypoint script
nohup ./entrypoint.sh > server.log 2>&1 &
```

To configure automatic restart on server reboot, set up a systemd service:
```ini
# /etc/systemd/system/autonomous-ai.service
[Unit]
Description=Autonomous AI Worker Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Autonomous-Ai-Worker
ExecStart=/home/ubuntu/Autonomous-Ai-Worker/entrypoint.sh
Restart=always
EnvironmentFile=/home/ubuntu/Autonomous-Ai-Worker/.env

[Install]
WantedBy=multi-user.target
```
Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autonomous-ai.service
```

---

## 3. Important Production Requirements & Checklist

| Requirement | Why it's needed | How it's handled |
| :--- | :--- | :--- |
| **Playwright System Deps** | Headless Chromium requires OS libraries (`libnss3`, `libasound2`, etc.). | Handled by `playwright install --with-deps chromium` in `Dockerfile`. |
| **Database Seeding** | Invoice INV-101 and vendor data must exist. | Handled automatically by `python mock_env/seed.py` on startup. |
| **Memory Allocation** | Headless browser execution requires minimum 512MB–1GB RAM. | Use instances with at least 1GB RAM (standard free/starter tiers on Render, Railway, or Fly.io). |
| **Ports 8001 & 8002** | Target mock portals for browser navigation. | Bound internally to `127.0.0.1` so they don't consume extra cloud endpoints. |
| **Port 8000** | Interactive dashboard and SSE trace stream. | Bound to `0.0.0.0` as the sole public HTTP endpoint. |

---

## 4. Deploying to Real Target Applications Instead of Mock Sites

If you ever transition this agent from a portfolio evaluation showcase to a real internal operations worker:
1. Update [config/environment.yaml](file:///Users/shekharnarayanmishra/Desktop/Autonomus%20AI/config/environment.yaml):
   ```yaml
   apps:
     - name: "Vendor Portal"
       base_url: "https://vendor.yourcompany.com"
       credentials:
         username: "${PROD_VENDOR_USER}"
         password: "${PROD_VENDOR_PASS}"
     - name: "Internal ERP"
       base_url: "https://erp.yourcompany.com"
       credentials:
         username: "${PROD_ERP_USER}"
         password: "${PROD_ERP_PASS}"
   ```
2. In that case, you do not run `mock_env` servers at all—the agent will navigate directly to your real corporate web portals.
