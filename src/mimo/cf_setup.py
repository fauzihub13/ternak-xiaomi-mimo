"""Cloudflare Email Routing setup via API.

Hindari klik-klik manual di dashboard. Bulk setup:
  1. Add destination address (Gmail Anda) + verify
  2. Create catch-all rule: *@domain.com -> Gmail

API yang dipakai:
  GET  /zones/{zone_id}/email/routing/rules            list rules
  POST /zones/{zone_id}/email/routing/rules            create rule
  GET  /accounts/{account_id}/email/routing/addresses  list destinations
  POST /accounts/{account_id}/email/routing/addresses  create destination

API token butuh permission:
  Account > Account Settings > Account Resources > Account > Edit
  Zone > Email Routing > Edit
  Zone > Zone > Read

Cara dapat credentials:
  1. Login Cloudflare dashboard
  2. Klik kanan domain di overview → "Copy Zone ID"
  3. Klik "Account ID" di sidebar kanan (pojok bawah) → copy
  4. Buka https://dash.cloudflare.com/profile/api-tokens
  5. Create Token > Custom Token > tambah permission di atas
"""

import argparse
import json
import os
import sys
import time
from typing import Any

import requests
from dotenv import load_dotenv


CF_API_BASE = "https://api.cloudflare.com/client/v4"


# ── HTTP helper ─────────────────────────────────────────────────────────────
def _headers(token: str, *, email: str | None = None,
             api_key: str | None = None) -> dict:
    """Build CF API auth headers.

    Dua mode auth:
      1. Bearer Token (scoped)  →  Authorization: Bearer <token>
      2. Global API Key         →  X-Auth-Email + X-Auth-Key

    Priority: kalau `email` AND `api_key` keduanya diset → mode 2.
    Else → mode 1 (token).
    """
    if email and api_key:
        return {
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        }
    if not token:
        raise ValueError("_headers butuh token, atau (email + api_key)")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class CFAuth:
    """Auth context for Cloudflare API calls.

    Dua mode:
      - Bearer (scoped token): token=<token>
      - Global API Key: token=<api_key>, email=<email>

    Usage:
        auth = CFAuth.from_env()  # baca dari env
        list_destinations(account_id, auth)
    """
    def __init__(self, token: str = "", email: str = "", api_key: str = ""):
        self.token = token
        self.email = email
        self.api_key = api_key
        if not self.token and (self.email and self.api_key):
            # Global API Key mode
            pass
        elif not self.token and not (self.email and self.api_key):
            raise ValueError("CFAuth butuh (token) atau (email + api_key)")

    @classmethod
    def from_env(cls) -> "CFAuth":
        """Bikin CFAuth dari env vars."""
        token = os.getenv("CF_API_TOKEN", "")
        api_key = os.getenv("CF_API_KEY", "")
        email = os.getenv("CF_EMAIL", "")
        if token:
            return cls(token=token)
        if api_key and email:
            return cls(api_key=api_key, email=email)
        raise ValueError(
            "Set CF_API_TOKEN (scoped) atau CF_API_KEY + CF_EMAIL (global)"
        )

    @property
    def mode(self) -> str:
        if self.token:
            return "token"
        return "global_api_key"

    def headers(self) -> dict:
        return _headers(self.token, email=self.email or None, api_key=self.api_key or None)


def _request(method: str, url: str, auth: "CFAuth | str", **kwargs) -> dict:
    """Auth-aware HTTP request. Backward compat: accept string as token."""
    if isinstance(auth, str):
        headers = _headers(auth)
    else:
        headers = auth.headers()
    r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    try:
        return r.json()
    except Exception:
        return {"success": False, "errors": [{"message": f"non-json response (HTTP {r.status_code})"}]}


def _request_legacy(method: str, url: str, token: str, **kwargs) -> dict:
    """Backward compat: pass token as Bearer."""
    r = requests.request(method, url, headers=_headers(token), timeout=30, **kwargs)
    try:
        return r.json()
    except Exception:
        return {"success": False, "errors": [{"message": f"non-json response (HTTP {r.status_code})"}]}


# ── Destinations ────────────────────────────────────────────────────────────
def list_destinations(account_id: str, auth: "CFAuth | str") -> list[dict]:
    """List semua destination addresses."""
    url = f"{CF_API_BASE}/accounts/{account_id}/email/routing/addresses"
    data = _request("GET", url, auth)
    if not data.get("success"):
        raise RuntimeError(f"list_destinations failed: {data.get('errors')}")
    return data.get("result", [])


def create_destination(account_id: str, auth: "CFAuth | str", email: str) -> dict:
    """Create destination address. Email butuh verifikasi manual oleh user."""
    url = f"{CF_API_BASE}/accounts/{account_id}/email/routing/addresses"
    data = _request("POST", url, auth, json={"email": email})
    if not data.get("success"):
        raise RuntimeError(f"create_destination failed: {data.get('errors')}")
    return data["result"]


def ensure_destination(account_id: str, auth: "CFAuth | str", email: str) -> dict:
    """Idempotent: create kalau belum ada, return existing kalau sudah."""
    for d in list_destinations(account_id, auth):
        if d.get("email", "").lower() == email.lower():
            return d
    return create_destination(account_id, auth, email)


# ── Routing rules ───────────────────────────────────────────────────────────
def list_rules(zone_id: str, auth: "CFAuth | str") -> list[dict]:
    """List semua routing rules."""
    url = f"{CF_API_BASE}/zones/{zone_id}/email/routing/rules"
    data = _request("GET", url, auth)
    if not data.get("success"):
        raise RuntimeError(f"list_rules failed: {data.get('errors')}")
    return data.get("result", [])


def create_rule(zone_id: str, auth: "CFAuth | str", *,
                name: str, matchers: list[dict], actions: list[dict],
                enabled: bool = True, priority: int = 0) -> dict:
    """Create satu routing rule."""
    url = f"{CF_API_BASE}/zones/{zone_id}/email/routing/rules"
    payload = {
        "name": name,
        "enabled": enabled,
        "priority": priority,
        "matchers": matchers,
        "actions": actions,
    }
    data = _request("POST", url, auth, json=payload)
    if not data.get("success"):
        raise RuntimeError(f"create_rule failed: {data.get('errors')}")
    return data["result"]


def delete_rule(zone_id: str, auth: "CFAuth | str", rule_id: str) -> dict:
    url = f"{CF_API_BASE}/zones/{zone_id}/email/routing/rules/{rule_id}"
    data = _request("DELETE", url, auth)
    if not data.get("success"):
        raise RuntimeError(f"delete_rule failed: {data.get('errors')}")
    return data


def ensure_catch_all(zone_id: str, auth: "CFAuth | str", dest_email: str) -> dict:
    """Idempotent: create catch-all rule kalau belum ada."""
    for rule in list_rules(zone_id, auth):
        matchers = rule.get("matchers", [])
        is_catchall = any(m.get("type") == "all" for m in matchers)
        if is_catchall:
            return rule  # already exists
    return create_rule(
        zone_id, auth,
        name="Catch-all to Gmail",
        matchers=[{"type": "all"}],
        actions=[{"type": "forward", "value": [dest_email]}],
        enabled=True,
        priority=0,
    )


# ── Zone / Account lookup ───────────────────────────────────────────────────
def list_zones(auth: "CFAuth | str", name: str | None = None) -> list[dict]:
    """List zones (domains). Filter by name kalau diberikan."""
    url = f"{CF_API_BASE}/zones?per_page=50"
    data = _request("GET", url, auth)
    if not data.get("success"):
        raise RuntimeError(f"list_zones failed: {data.get('errors')}")
    zones = data.get("result", [])
    if name:
        zones = [z for z in zones if name.lower() in z.get("name", "").lower()]
    return zones


def get_account_id(auth: "CFAuth | str") -> str:
    """Ambil Account ID dari token user info."""
    url = f"{CF_API_BASE}/user"
    data = _request("GET", url, auth)
    if not data.get("success"):
        raise RuntimeError(f"get_account_id failed: {data.get('errors')}")
    # 'accounts' may contain multiple; ambil yang 'type' == 'standard' atau first
    accounts = data["result"].get("accounts", [])
    if not accounts:
        raise RuntimeError("no accounts found for token")
    return accounts[0]["id"]


# ── Setup orchestration ─────────────────────────────────────────────────────
def setup_catch_all(auth: "CFAuth | str", zone_id: str, account_id: str,
                    dest_email: str, dry_run: bool = False) -> dict:
    """One-shot setup: ensure destination + catch-all rule exists.

    Returns {"destination": {...}, "rule": {...}, "already_existed": bool}
    """
    result = {"destination": None, "rule": None, "already_existed": False}

    # 1. Destination
    existing_dests = list_destinations(account_id, auth)
    dest = next((d for d in existing_dests if d["email"].lower() == dest_email.lower()), None)
    if dest:
        print(f"  ✓ destination already exists: {dest['email']} (verified={dest.get('verified')})")
        result["destination"] = dest
    else:
        if dry_run:
            print(f"  [dry-run] would create destination: {dest_email}")
            dest = {"email": dest_email, "verified": True, "dry_run": True}
            result["destination"] = dest
        else:
            print(f"  creating destination: {dest_email}…")
            dest = create_destination(account_id, auth, dest_email)
            print(f"  ✓ destination created (cek email untuk verifikasi)")
            result["destination"] = dest

    if not dest.get("verified", False) and not dry_run:
        print(f"  ⚠ destination belum verified — buka email {dest_email} dan klik link")
        print(f"     Setelah verified, run ulang script ini untuk create catch-all rule")

    # 2. Catch-all rule (skip kalau destination belum verified, agar tidak silently gagal)
    if not dest.get("verified", False) and not dry_run:
        print("  → skip catch-all creation (destination belum verified)")
        return result

    existing_rules = list_rules(zone_id, auth)
    catchall = next((r for r in existing_rules
                     if any(m.get("type") == "all" for m in r.get("matchers", []))), None)
    if catchall:
        print(f"  ✓ catch-all rule sudah ada: id={catchall['id']} name='{catchall.get('name')}'")
        result["rule"] = catchall
        result["already_existed"] = True
    else:
        if dry_run:
            print(f"  [dry-run] would create catch-all rule → {dest_email}")
            result["rule"] = {"name": "Catch-all to Gmail", "dry_run": True}
        else:
            print(f"  creating catch-all rule → {dest_email}…")
            rule = create_rule(
                zone_id, auth,
                name="Catch-all to Gmail",
                matchers=[{"type": "all"}],
                actions=[{"type": "forward", "value": [dest_email]}],
            )
            print(f"  ✓ catch-all rule created: id={rule['id']}")
            result["rule"] = rule
    return result


# ── Status print ────────────────────────────────────────────────────────────
def print_status(auth: "CFAuth | str", zone_id: str, account_id: str) -> None:
    print("=" * 60)
    print("Cloudflare Email Routing — Status")
    print("=" * 60)
    print(f"Zone ID   : {zone_id}")
    print(f"Account ID: {account_id}")
    print(f"Auth mode : {auth.mode}")
    print()
    print("Destinations:")
    dests = list_destinations(account_id, auth)
    if not dests:
        print("  (none)")
    for d in dests:
        v = "✓" if d.get("verified") else "✗"
        print(f"  {v} {d['email']}  (created={d.get('created','')[:10]})")
    print()
    print("Routing rules:")
    rules = list_rules(zone_id, auth)
    if not rules:
        print("  (none)")
    for r in rules:
        en = "✓" if r.get("enabled") else "✗"
        matchers = ",".join(f"{m.get('type')}={m.get('field','')}{m.get('value','')}"
                            for m in r.get("matchers", []))
        actions = ",".join(f"{a.get('type')}={a.get('value','')}"
                           for a in r.get("actions", []))
        print(f"  {en} [{r.get('priority', 0)}] {r.get('name')}  match: {matchers}  → {actions}")
    print("=" * 60)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    load_dotenv()
    ap = argparse.ArgumentParser(
        description="Setup Cloudflare Email Routing via API (bulk, no dashboard)",
    )
    ap.add_argument("--token", default=os.getenv("CF_API_TOKEN", ""),
                    help="Cloudflare API token (atau set CF_API_TOKEN)")
    ap.add_argument("--api-key", default=os.getenv("CF_API_KEY", ""),
                    help="Global API Key (fallback: dengan --email)")
    ap.add_argument("--email", default=os.getenv("CF_EMAIL", ""),
                    help="Email akun Cloudflare (untuk Global API Key)")
    ap.add_argument("--zone-id", default=os.getenv("CF_ZONE_ID", ""),
                    help="Zone ID (atau set CF_ZONE_ID)")
    ap.add_argument("--account-id", default=os.getenv("CF_ACCOUNT_ID", ""),
                    help="Account ID (auto-detect kalau kosong)")
    ap.add_argument("--domain",
                    help="Domain untuk lookup zone ID (alternatif dari --zone-id)")
    ap.add_argument("--dest", default=os.getenv("CF_DEST_EMAIL", ""),
                    help="destination email (Gmail Anda)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="cetak status rules & destinations")
    g.add_argument("--setup", action="store_true",
                   help="setup catch-all (default kalau tidak ada flag)")
    g.add_argument("--list-zones", action="store_true",
                   help="cetak semua zone di account")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Resolve auth
    try:
        if args.token:
            auth = CFAuth(token=args.token)
        elif args.api_key and args.email:
            auth = CFAuth(api_key=args.api_key, email=args.email)
        else:
            auth = CFAuth.from_env()
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        print("Set salah satu:", file=sys.stderr)
        print("  - CF_API_TOKEN  (scoped API Token)", file=sys.stderr)
        print("  - CF_API_KEY + CF_EMAIL  (Global API Key + email login)", file=sys.stderr)
        sys.exit(1)
    print(f"[*] auth mode: {auth.mode}")

    # Resolve zone_id & account_id
    if not args.account_id:
        print("[*] detecting account_id dari token…")
        args.account_id = get_account_id(auth)
        print(f"    account_id = {args.account_id}")

    if args.list_zones:
        print("Zones:")
        for z in list_zones(auth):
            print(f"  {z['name']:<40}  id={z['id']}")
        return

    if not args.zone_id:
        if not args.domain:
            print("Set --zone-id atau --domain (atau env CF_ZONE_ID)",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[*] mencari zone untuk domain='{args.domain}'…")
        zones = list_zones(auth, args.domain)
        if not zones:
            print(f"  domain '{args.domain}' tidak ditemukan di account ini",
                  file=sys.stderr)
            sys.exit(1)
        args.zone_id = zones[0]["id"]
        print(f"    zone_id = {args.zone_id} (domain: {zones[0]['name']})")

    # Action
    if args.status:
        print_status(auth, args.zone_id, args.account_id)
        return

    # Setup (default)
    if not args.dest:
        print("Set --dest=<email> (atau env CF_DEST_EMAIL) untuk setup catch-all",
              file=sys.stderr)
        sys.exit(1)
    print(f"[*] setup catch-all → {args.dest}")
    result = setup_catch_all(
        auth, args.zone_id, args.account_id,
        args.dest, dry_run=args.dry_run,
    )
    if result["rule"]:
        print()
        print("✓ Setup selesai.")
        print(f"  Test: kirim email ke test123@{args.domain or '(domain)'}")
        print(f"  Verifikasi via: python -m mimo.setup_test")


if __name__ == "__main__":
    main()