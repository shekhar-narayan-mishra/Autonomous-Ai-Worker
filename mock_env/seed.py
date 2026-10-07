import sqlite3
import os

def seed_vendor_portal():
    os.makedirs("mock_env/vendor_portal", exist_ok=True)
    conn = sqlite3.connect("mock_env/vendor_portal/data.db")
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS invoices")
    c.execute("CREATE TABLE invoices (id TEXT, amount REAL, due_date TEXT, vendor TEXT, status TEXT)")
    invoices = [
        ("INV-100", 500.00, "2024-11-01", "Acme Corp", "unpaid"),
        ("INV-101", 1200.50, "2024-11-15", "Acme Corp", "unpaid"),
        ("INV-200", 850.00, "2024-11-20", "TechFlow", "unpaid"),
        ("INV-AMB-1", 300.00, "2024-12-01", "Globex", "unpaid"),
        ("INV-AMB-2", 300.00, "2024-12-01", "Globex", "unpaid"),
    ]
    c.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?)", invoices)
    
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("CREATE TABLE users (username TEXT, password TEXT)")
    c.execute("INSERT INTO users VALUES ('admin', 'password123')")
    
    conn.commit()
    conn.close()

def seed_erp():
    os.makedirs("mock_env/erp", exist_ok=True)
    conn = sqlite3.connect("mock_env/erp/erp.db")
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS bills")
    c.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, invoice_id TEXT, amount REAL, due_date TEXT, vendor TEXT, notes TEXT)")
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("CREATE TABLE users (username TEXT, password TEXT)")
    c.execute("INSERT INTO users VALUES ('admin', 'admin')")
    c.execute("INSERT INTO bills (invoice_id, amount, due_date, vendor, notes) VALUES ('INV-100', 500.0, '2024-10-01', 'Acme Corp', '')")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    seed_vendor_portal()
    seed_erp()
    print("Seeded mock environments.")
