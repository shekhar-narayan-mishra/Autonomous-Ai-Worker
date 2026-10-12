import os
import httpx
import config
from mock_env.seed import seed_vendor_portal, seed_erp


def get_active_profile_name() -> str:
    env_file = config.get_environment_file()
    env_data = config.load_environment()
    apps = env_data.get("apps", [])
    for app in apps:
        if "onrender.com" in app.get("base_url", ""):
            return "Render apps"
    if "render" in env_file.lower():
        return "Render apps"
    return "Local"


def get_app_base_url(app_name: str) -> str:
    env_data = config.load_environment()
    for app in env_data.get("apps", []):
        if app.get("name", "").lower() == app_name.lower():
            return app.get("base_url", "").rstrip("/")
    if "vendor" in app_name.lower():
        return "http://localhost:8001"
    if "erp" in app_name.lower():
        return "http://localhost:8002"
    return "http://localhost:8000"


def is_local_url(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def reset_app(app_name: str, base_url: str, profile: str = "base"):
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    headers = {"X-Admin-Token": admin_token} if admin_token else {}
    reset_url = f"{base_url.rstrip('/')}/admin/reset?profile={profile}"

    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(reset_url, headers=headers)
            if res.status_code == 200:
                return
            elif res.status_code == 403:
                raise RuntimeError(
                    f"Admin reset rejected by {app_name} at {reset_url}: 403 Forbidden (check ADMIN_TOKEN)"
                )
            else:
                raise RuntimeError(
                    f"Admin reset failed on {app_name} at {reset_url}: HTTP {res.status_code} ({res.text})"
                )
    except httpx.RequestError as exc:
        if is_local_url(base_url):
            # Local profile fallback: if local server isn't running or endpoint failed, seed local sqlite directly
            if "vendor" in app_name.lower():
                seed_vendor_portal(profile)
            elif "erp" in app_name.lower():
                seed_erp(profile)
            return
        raise RuntimeError(f"Could not connect to {app_name} at {reset_url}: {exc}")


def reset_all_apps(profile: str = "base"):
    env_data = config.load_environment()
    apps = env_data.get("apps", [])
    if not apps:
        apps = [
            {"name": "Vendor Portal", "base_url": "http://localhost:8001"},
            {"name": "Internal ERP", "base_url": "http://localhost:8002"},
        ]
    for app in apps:
        name = app.get("name", "")
        base_url = app.get("base_url", "")
        if base_url:
            reset_app(name, base_url, profile)


def snapshot_erp_bills() -> list[dict]:
    erp_url = get_app_base_url("Internal ERP")
    bills_endpoint = f"{erp_url}/api/bills"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(bills_endpoint)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return []


def check_preconditions(profile: str = "base") -> tuple[bool, str, list[dict]]:
    erp_url = get_app_base_url("Internal ERP")
    portal_url = get_app_base_url("Vendor Portal")

    erp_bills = []
    with httpx.Client(timeout=10.0) as client:
        # 1. Query ERP bills
        bills_endpoint = f"{erp_url}/api/bills"
        try:
            r = client.get(bills_endpoint)
            if r.status_code != 200:
                return False, f"Failed to query Internal ERP bills at {bills_endpoint}: HTTP {r.status_code}", []
            erp_bills = r.json()
        except Exception as e:
            return False, f"Failed to connect to Internal ERP at {bills_endpoint}: {e}", []

        # Assert ERP preconditions
        if profile == "base":
            matching_inv101 = [b for b in erp_bills if b.get("invoice_id") == "INV-101"]
            if matching_inv101:
                return (
                    False,
                    f"Precondition failed for Internal ERP at {bills_endpoint}: expected no bill for INV-101 in base profile, found {len(matching_inv101)}",
                    erp_bills,
                )
        elif profile == "empty_erp":
            if len(erp_bills) > 0:
                return (
                    False,
                    f"Precondition failed for Internal ERP at {bills_endpoint}: expected empty ERP, found {len(erp_bills)} bill(s)",
                    erp_bills,
                )

        # 2. Query Vendor Portal invoices
        invoices_endpoint = f"{portal_url}/api/invoices"
        try:
            r = client.get(invoices_endpoint)
            if r.status_code != 200:
                return False, f"Failed to query Vendor Portal invoices at {invoices_endpoint}: HTTP {r.status_code}", erp_bills
            invoices = r.json()
        except Exception as e:
            return False, f"Failed to connect to Vendor Portal at {invoices_endpoint}: {e}", erp_bills

        # Assert Portal preconditions
        if profile == "base":
            has_inv101 = any(i.get("id") == "INV-101" for i in invoices)
            if not has_inv101:
                return (
                    False,
                    f"Precondition failed for Vendor Portal at {invoices_endpoint}: expected invoice INV-101 in base profile, but not found",
                    erp_bills,
                )
        elif profile == "no_invoices":
            if len(invoices) > 0:
                return (
                    False,
                    f"Precondition failed for Vendor Portal at {invoices_endpoint}: expected no invoices, found {len(invoices)}",
                    erp_bills,
                )

    return True, "Preconditions verified successfully", erp_bills
