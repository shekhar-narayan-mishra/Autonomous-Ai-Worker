import os
import sqlite3
import sys

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chaos

app = FastAPI(title="Internal ERP")

def get_db():
    conn = sqlite3.connect("mock_env/erp/erp.db")
    conn.row_factory = sqlite3.Row
    return conn

# Auto-seed if the db is empty
try:
    _conn = get_db()
    _conn.execute("SELECT 1 FROM users")
    _conn.close()
except sqlite3.OperationalError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from mock_env.seed import seed_erp
        seed_erp("base")
    except ImportError:
        pass

@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    if chaos.is_active("flaky_500_erp"):
        import random
        if random.random() < 0.3:
            return HTMLResponse("Internal Server Error", status_code=500)
    response = await call_next(request)
    return response

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    html = f"""
    <html><body>
        <h1>ERP Login</h1>
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
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(key="erp_session", value="valid_token")
        return response
    return RedirectResponse(url="/?error=Invalid credentials", status_code=302)

def check_auth(request: Request):
    return request.cookies.get("erp_session") == "valid_token"

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=302)
        
    conn = get_db()
    bills = conn.execute("SELECT * FROM bills").fetchall()
    
    rows = ""
    for b in bills:
        rows += f"<tr><td>{b['id']}</td><td>{b['invoice_id']}</td><td>{b['vendor']}</td><td>{b['amount']}</td><td>{b['due_date']}</td><td>{b['notes']}</td></tr>"
        
    html = f"""
    <html><body>
        <h1>ERP Dashboard</h1>
        <a href="/add_bill" id="add-bill-link">Add Bill</a>
        <h2>Bills</h2>
        <table id="bills-list">
            <tr><th>ID</th><th>Invoice ID</th><th>Vendor</th><th>Amount</th><th>Due Date</th><th>Notes</th></tr>
            {rows}
        </table>
    </body></html>
    """
    return html

@app.get("/add_bill", response_class=HTMLResponse)
async def add_bill_page(request: Request, error: str = "", success: str = ""):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=302)
        
    html = f"""
    <html><body>
        <h1>Add New Bill</h1>
        {f'<p style="color:red" id="error-msg">{error}</p>' if error else ''}
        {f'<p style="color:green" id="success-msg">{success}</p>' if success else ''}
        <form method="post" action="/add_bill">
            <label>Invoice ID:</label> <input type="text" name="invoice_id" id="invoice_id" /><br/>
            <label>Vendor:</label> <input type="text" name="vendor" id="vendor" /><br/>
            <label>Amount:</label> <input type="text" name="amount" id="amount" /><br/>
            <label>Due Date:</label> <input type="text" name="due_date" id="due_date" /><br/>
            <label>Notes:</label> <input type="text" name="notes" id="notes" /><br/>
            <button type="submit" id="submit-btn">Submit</button>
        </form>
        <br/>
        <a href="/dashboard" id="back-link">Back to Dashboard</a>
    </body></html>
    """
    return html

@app.post("/add_bill")
async def add_bill(
    request: Request,
    invoice_id: str = Form(""),
    vendor: str = Form(""),
    amount: str = Form(""),
    due_date: str = Form(""),
    notes: str = Form("")
):
    if not check_auth(request):
        return RedirectResponse(url="/", status_code=302)
        
    if chaos.is_active("validation_error_on_first_submit"):
        flags = chaos.get_chaos()
        flags["validation_error_on_first_submit"] = False
        chaos.set_chaos(flags)
        return RedirectResponse(url="/add_bill?error=System%20Validation%20Failed.%20Please%20try%20again.", status_code=302)
        
    if chaos.is_active("missing_field") and not invoice_id:
        return RedirectResponse(url="/add_bill?error=Invoice%20ID%20is%20required", status_code=302)
        
    try:
        amt = float(amount)
    except ValueError:
        return RedirectResponse(url="/add_bill?error=Invalid%20Amount", status_code=302)
        
    conn = get_db()
    conn.execute("INSERT INTO bills (invoice_id, vendor, amount, due_date, notes) VALUES (?, ?, ?, ?, ?)", 
                 (invoice_id, vendor, amt, due_date, notes))
    conn.commit()
    
    return RedirectResponse(url="/add_bill?success=Bill%20added%20successfully", status_code=302)

@app.get("/api/verify/{invoice_id}")
async def verify_bill(invoice_id: str):
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE invoice_id=?", (invoice_id,)).fetchone()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    return dict(bill)
