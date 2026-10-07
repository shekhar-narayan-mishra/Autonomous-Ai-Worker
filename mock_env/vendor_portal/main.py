import os
import sqlite3
import sys
import time

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chaos

app = FastAPI(title="Vendor Portal")

def get_db():
    conn = sqlite3.connect("mock_env/vendor_portal/data.db")
    conn.row_factory = sqlite3.Row
    return conn

@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    if chaos.is_active("flaky_500"):
        import random
        if random.random() < 0.3:
            return HTMLResponse("Internal Server Error", status_code=500)
    if chaos.is_active("slow_load"):
        time.sleep(2)
    response = await call_next(request)
    return response

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    html = f"""
    <html><body>
        <h1>Vendor Portal Login</h1>
        {f'<p style="color:red">{error}</p>' if error else ''}
        <form method="post" action="/login">
            <input type="text" name="username" placeholder="Username" id="username"/>
            <input type="password" name="password" placeholder="Password" id="password"/>
            <button type="submit" id="login-btn">Login</button>
        </form>
    </body></html>
    """
    return html

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    if user:
        response = RedirectResponse(url="/invoices", status_code=302)
        response.set_cookie(key="session", value="valid_token")
        return response
    return RedirectResponse(url="/?error=Invalid credentials", status_code=302)

def check_auth(request: Request):
    session = request.cookies.get("session")
    if not session or chaos.is_active("expired_session"):
        if chaos.is_active("expired_session"):
            flags = chaos.get_chaos()
            flags["expired_session"] = False
            chaos.set_chaos(flags)
        return False
    return True

@app.get("/invoices", response_class=HTMLResponse)
async def invoices_page(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=302)
    
    conn = get_db()
    invoices = conn.execute("SELECT * FROM invoices").fetchall()
    
    rows = ""
    for inv in invoices:
        rows += f"<tr><td>{inv['id']}</td><td>{inv['vendor']}</td><td>{inv['due_date']}</td><td><a href='/invoice/{inv['id']}' id='view-{inv['id']}'>View</a></td></tr>"
    
    popup = ""
    if chaos.is_active("random_popup_modal"):
        popup = """
        <div id="annoying-modal" style="position:fixed; top:50%; left:50%; background:white; border:1px solid black; padding:20px; z-index:1000;">
            <h2>Special Offer!</h2>
            <button id="close-modal" onclick="document.getElementById('annoying-modal').style.display='none'">Close</button>
        </div>
        """
        
    html = f"""
    <html><body>
        <h1>Invoices</h1>
        {popup}
        <table id="invoice-list">
            <tr><th>ID</th><th>Vendor</th><th>Due Date</th><th>Action</th></tr>
            {rows}
        </table>
    </body></html>
    """
    return html

@app.get("/invoice/{id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, id: str):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=302)
        
    conn = get_db()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (id,)).fetchone()
    if not inv:
        raise HTTPException(status_code=404)
        
    html = f"""
    <html><body>
        <h1>Invoice Details</h1>
        <p><strong>ID:</strong> <span id="inv-id">{inv['id']}</span></p>
        <p><strong>Vendor:</strong> <span id="inv-vendor">{inv['vendor']}</span></p>
        <p><strong>Amount:</strong> $<span id="inv-amount">{inv['amount']}</span></p>
        <p><strong>Due Date:</strong> <span id="inv-due-date">{inv['due_date']}</span></p>
        <p><strong>Status:</strong> <span id="inv-status">{inv['status']}</span></p>
        <a href="/invoices" id="back-link">Back</a>
    </body></html>
    """
    return html
