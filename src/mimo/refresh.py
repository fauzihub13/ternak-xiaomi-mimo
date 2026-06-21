"""Refresh cookies untuk akun Xiaomi yang sudah ada.

Cookie Xiaomi (passToken, serviceToken) TTL pendek (~jam sampai hari).
Daripada register ulang, cukup login ulang pakai email+password untuk
refresh cookies, validasi dengan hit endpoint MiMo, dan update JSONL.

Usage:
    # Refresh 1 akun
    python -m mimo.refresh --account accounts.jsonl --row 0

    # Refresh semua akun sukses di JSONL
    python -m mimo.refresh --all --account accounts.jsonl

    # Custom output
    python -m mimo.refresh --all --account accounts.jsonl --out accounts_fresh.jsonl

    # In-place update
    python -m mimo.refresh --all --account accounts.jsonl --in-place
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

from .bot import login_xiaomi


# ── Konstanta ────────────────────────────────────────────────────────────────
MIMO_BASE = "https://platform.xiaomimimo.com"
MIMO_USER_PROFILE = f"{MIMO_BASE}/api/v1/userProfile"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

USE_PROXY = os.getenv("USE_PROXY", "1") == "1"
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:40000")


# ── Helpers ─────────────────────────────────────────────────────────────────
def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    out = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  [warn] line {i} not valid JSON: {e}", file=sys.stderr)
    return out


def save_jsonl(accounts: list[dict], path: str | Path) -> None:
    p = Path(path)
    p.write_text("\n".join(json.dumps(a) for a in accounts) + "\n")
    print(f"  [saved] {len(accounts)} records → {p.absolute()}")


# ── Cookie validation ───────────────────────────────────────────────────────
def validate_cookies(cookies: dict, *, timeout: int = 15) -> tuple[bool, dict]:
    """Hit MiMo /api/v1/userProfile untuk validasi cookies.
    Returns (valid, profile_data) — valid=True kalau HTTP 200 + code==0.
    """
    if not cookies:
        return False, {}
    session = cffi_requests.Session(
        impersonate="chrome124",
        proxies={"https": PROXY_URL, "http": PROXY_URL} if USE_PROXY else None,
    )
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    # Inject cookies (camelCase names from mimo.bot login result)
    for name, value in cookies.items():
        if not value:
            continue
        # Map MiMo cookies to platform.xiaomimimo.com
        if name in ("api-platform_ph", "api-platform_serviceToken"):
            session.cookies.set(name, value, domain="platform.xiaomimimo.com")
        else:
            session.cookies.set(name, value, domain=".xiaomi.com")

    try:
        resp = session.get(
            MIMO_USER_PROFILE,
            headers={"Referer": f"{MIMO_BASE}/console/profile"},
            timeout=timeout,
        )
    except Exception as e:
        return False, {"error": f"network: {e}"}

    if resp.status_code != 200:
        return False, {"error": f"http {resp.status_code}", "body": resp.text[:200]}
    try:
        data = resp.json()
    except Exception:
        return False, {"error": "non-json", "body": resp.text[:200]}
    if data.get("code") != 0:
        return False, {"error": f"code={data.get('code')}", "body": data}
    return True, data.get("data", {})


# ── Refresh single account ───────────────────────────────────────────────────
def refresh_account(account: dict, *, validate: bool = True,
                    dry_run: bool = False) -> dict:
    """Re-login 1 akun, return updated record (status: refreshed|failed)."""
    email = account.get("email")
    password = account.get("password")
    if not email or not password:
        return {
            **account,
            "status": "failed",
            "error": "missing email/password in record",
            "refresh_attempted_at": utcnow_iso(),
        }

    if dry_run:
        return {
            **account,
            "status": "dry_run",
            "refresh_attempted_at": utcnow_iso(),
        }

    login_data = login_xiaomi(email, password, dry_run=False)
    if not login_data:
        return {
            **account,
            "status": "failed",
            "error": "login failed (captcha? wrong password? account locked?)",
            "refresh_attempted_at": utcnow_iso(),
        }

    new_cookies = login_data.get("cookies", {})
    new_record = {
        **account,
        "cookies": new_cookies,
        "passToken":    login_data.get("passToken", ""),
        "serviceToken": login_data.get("serviceToken", ""),
        "userId":       login_data.get("userId", ""),
        "cUserId":      login_data.get("cUserId", ""),
        "status":       "success",
        "refreshed_at": utcnow_iso(),
    }

    if validate:
        ok, profile = validate_cookies(new_cookies)
        new_record["validated"] = ok
        new_record["validation_profile"] = profile
        if not ok:
            new_record["status"] = "unvalidated"
            new_record["validation_error"] = profile.get("error", "unknown")

    return new_record


# ── Refresh batch ───────────────────────────────────────────────────────────
def refresh_all(accounts: list[dict], *, validate: bool = True,
                delay: float = 0, dry_run: bool = False) -> list[dict]:
    """Refresh semua akun status==success di accounts."""
    out = []
    for i, acc in enumerate(accounts):
        if acc.get("status") != "success":
            out.append(acc)  # keep as-is
            continue
        print(f"\n[{i + 1}/{len(accounts)}] refresh {acc.get('email')}")
        new_acc = refresh_account(acc, validate=validate, dry_run=dry_run)
        if new_acc.get("status") in ("success", "unvalidated", "dry_run"):
            print(f"  ✓ {new_acc['status']}")
        else:
            print(f"  ✗ {new_acc.get('error')}")
        out.append(new_acc)
        if delay > 0 and i < len(accounts) - 1:
            time.sleep(delay)
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    load_dotenv()
    ap = argparse.ArgumentParser(
        description="Refresh cookies untuk akun Xiaomi existing (no register ulang)",
    )
    ap.add_argument("--account", required=True, help="path ke JSONL")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--row", type=int, help="refresh specific row (0-indexed)")
    g.add_argument("--all", action="store_true", help="refresh semua akun success")
    ap.add_argument("--out", help="output file (default: in-place update)")
    ap.add_argument("--in-place", action="store_true",
                    help="update file langsung (default kalau --out tidak diset)")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip validasi MiMo /userProfile (lebih cepat)")
    ap.add_argument("--delay", type=float, default=0,
                    help="delay antar akun (detik, default 0)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    accounts = load_jsonl(args.account)
    if not accounts:
        print("file kosong", file=sys.stderr)
        sys.exit(1)

    # Determine target records
    if args.all:
        targets = list(range(len(accounts)))
    else:
        targets = [args.row]

    # Snapshot original records
    original = [dict(a) for a in accounts]
    # Refresh
    for idx in targets:
        if accounts[idx].get("status") != "success":
            print(f"[skip] row {idx}: status={accounts[idx].get('status')}")
            continue
        print(f"\n[row {idx}] {accounts[idx].get('email')}")
        new_acc = refresh_account(
            accounts[idx],
            validate=not args.no_validate,
            dry_run=args.dry_run,
        )
        accounts[idx] = new_acc
        if new_acc.get("status") in ("success", "unvalidated", "dry_run"):
            print(f"  ✓ {new_acc['status']}")
        else:
            print(f"  ✗ {new_acc.get('error')}")
        if args.delay > 0 and idx != targets[-1]:
            time.sleep(args.delay)

    # Save output
    if not args.dry_run:
        if args.out:
            out_path = args.out
        else:
            out_path = args.account  # in-place
        save_jsonl(accounts, out_path)

    # Summary
    refreshed = sum(1 for i, a in enumerate(accounts)
                    if i in targets and a.get("status") == "success")
    unval = sum(1 for i, a in enumerate(accounts)
                if i in targets and a.get("status") == "unvalidated")
    failed = sum(1 for i, a in enumerate(accounts)
                 if i in targets and a.get("status") == "failed")
    print()
    print("=" * 50)
    print(f"Refreshed: {refreshed}")
    print(f"Unvalidated (cookies OK tapi validate fail): {unval}")
    print(f"Failed: {failed}")
    print("=" * 50)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()