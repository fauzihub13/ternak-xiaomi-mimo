"""End-to-end orchestrator: register → SSO → MiMo profile → API key (no bind_referral).

Single entry point: `python -m mimo.e2e`.

Flow:
  Step 1: mimo.register.register()           — daftar akun baru (8 steps)
  Step 2: mimo.bot.login_with_cookies()       — pakai passToken existing
  Step 3: mimo.bot.sso_to_mimo()              — full redirect chain ke MiMo
  Step 4: GET /api/v1/userProfile             — verify session + load data
  Step 5 (optional): POST /api/v1/apiKeys     — create API key
"""

import argparse
import json
import os
import random
import string
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from .register import register, RegisterError
from .bot import (
    login_with_cookies,
    sso_to_mimo,
    MIMO_BASE,
    make_session,
)
from curl_cffi import requests as cffi_requests

load_dotenv()


def _generate_email() -> str | None:
    """Generate random email untuk catch-all domain dari `EMAIL_DOMAIN` env.

    Format: <10-char-random-alnum>@<domain>
    Returns None kalau EMAIL_DOMAIN tidak di-set.
    """
    domain = os.getenv("EMAIL_DOMAIN", "").strip()
    if not domain:
        return None
    # Strip protocol kalau ada
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{local}@{domain}"


def _default_password() -> str:
    """Password dari `XIAOMI_PASSWORD` env, atau generate random."""
    pw = os.getenv("XIAOMI_PASSWORD", "").strip()
    if pw:
        return pw
    # Random fallback
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(random.choices(chars, k=18))


def load_account_file(path: str, row: int = 0) -> dict:
    """Load akun dari JSON atau JSONL file (auto-detect)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    text = p.read_text().strip()
    if not text:
        raise ValueError(f"empty file: {path}")

    rows = []

    # Detect JSON vs JSONL
    if text.startswith("["):
        # JSON array
        try:
            arr = json.loads(text)
            rows = [o for o in arr if o.get("status") == "success"]
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON array: {e}")
    elif "\n" in text and not text.startswith("{"):
        # Pure JSONL
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("status") == "success":
                    rows.append(obj)
            except json.JSONDecodeError as e:
                print(f"  [warn] line {i} invalid JSON: {e}")
    else:
        # Single JSON object (might be one record or array)
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                rows = [o for o in obj if o.get("status") == "success"]
            elif isinstance(obj, dict):
                # Single record — accept even without 'status' field
                if obj.get("status") == "success" or "cookies" in obj:
                    rows = [obj]
                else:
                    rows = [obj]  # still try
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}")

    if not rows:
        raise ValueError(f"no success records in {path}")
    if row >= len(rows):
        raise IndexError(f"row {row} out of range (have {len(rows)} records)")
    return rows[row]


def load_profile(session) -> dict:
    """GET /api/v1/userProfile — verify session + return user data."""
    print("  [4/6] GET /api/v1/userProfile…", end=" ")
    r = session.get(f"{MIMO_BASE}/api/v1/userProfile", impersonate="chrome124")
    if r.status_code != 200:
        raise RuntimeError(f"profile failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"profile error code={data.get('code')}: {data.get('message', data)}")
    profile = data["data"]
    print(f"✓ userId={profile['userId']}")
    return profile


def check_agreement(session) -> bool:
    """Hit /api/v1/agreement + verify profile.agreement=true.

    Returns True kalau agreement sudah di-accept (profile.agreement == true).
    Returns False kalau agreement belum di-accept (akan skip API key creation).
    Raises RuntimeError kalau gagal hit endpoint.
    """
    # 1. GET /api/v1/agreement — verify endpoint reachable
    print("  [5a] GET /api/v1/agreement…", end=" ")
    jar = getattr(session.cookies, "jar", session.cookies)
    ph = next((c.value for c in jar if c.name == "api-platform_ph"), None)
    if not ph:
        raise RuntimeError("no api-platform_ph cookie (SSO may have failed)")

    url = f"{MIMO_BASE}/api/v1/agreement?api-platform_ph={quote(ph)}"
    r = session.get(url, impersonate="chrome124")
    if r.status_code != 200:
        raise RuntimeError(f"agreement endpoint failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"agreement endpoint error code={data.get('code')}: {data.get('message', data)}")
    print(f"✓ (code={data['code']})")

    # 2. Check actual status via userProfile (agreement field)
    print("  [5b] GET /api/v1/userProfile (check agreement)…", end=" ")
    r = session.get(f"{MIMO_BASE}/api/v1/userProfile", impersonate="chrome124")
    if r.status_code != 200:
        raise RuntimeError(f"profile check failed: {r.status_code}")
    profile = r.json().get("data", {})
    agreed = profile.get("agreement", False)
    if agreed:
        print(f"✓ agreement=true")
    else:
        print(f"⚠ agreement=false (perlu accept manual via dashboard)")
    return agreed


def save_account_to_files(account: dict, profile: dict, api_key_data: dict = None) -> None:
    """Save account ke xiaomi_account.json (array) + accounts.txt (pipe-separated).

    xiaomi_account.json : JSON array of {email, password, cookies, profile, api_key, timestamp}
    accounts.txt       : pipe-separated 'email|password|apiKey' per line

    Appends (tidak overwrite). Kalau file belum ada → create baru.
    """
    now_iso = json.dumps({"ts": "now"})  # placeholder; use proper timestamp
    import datetime as _dt
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = {
        "email":      account["email"],
        "password":   account["password"],
        "cookies":    account.get("cookies", {}),
        "profile":    profile,
        "api_key":    api_key_data,
        "created_at": ts,
    }

    # ── xiaomi_account.json : append to array ──────────────────────────
    json_path = Path("xiaomi_account.json")
    if json_path.exists():
        try:
            existing = json.loads(json_path.read_text())
            if not isinstance(existing, list):
                # Convert old single-object format to array
                existing = [existing]
        except json.JSONDecodeError:
            existing = []
    else:
        existing = []

    # Replace kalau email sudah ada (update), else append
    existing = [r for r in existing if r.get("email") != account["email"]]
    existing.append(record)
    json_path.write_text(json.dumps(existing, indent=2))
    try:
        json_path.chmod(0o600)
    except Exception:
        pass
    print(f"  [saved] {json_path} ({len(existing)} akun total)")

    # ── accounts.txt : email|password|apikey per line ───────────────────
    txt_path = Path("accounts.txt")
    # Read existing
    if txt_path.exists():
        lines = [l for l in txt_path.read_text().splitlines() if l.strip()]
        # Replace kalau email sudah ada
        lines = [l for l in lines if not l.startswith(account["email"] + "|")]
    else:
        lines = []
    # Add new line
    api_key_str = api_key_data.get("apiKey", "") if api_key_data else "no-api-key-created"
    lines.append(f"{account['email']}|{account['password']}|{api_key_str}")
    txt_path.write_text("\n".join(lines) + "\n")
    try:
        txt_path.chmod(0o600)
    except Exception:
        pass
    print(f"  [saved] {txt_path} ({len(lines)} akun total)")


def list_api_keys(session) -> list[dict]:
    """GET /api/v1/apiKeys — list existing API keys."""
    print("  [list] GET /api/v1/apiKeys…", end=" ")
    r = session.get(f"{MIMO_BASE}/api/v1/apiKeys", impersonate="chrome124")
    if r.status_code != 200:
        raise RuntimeError(f"list apiKeys failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"list apiKeys error: {data.get('message', data)}")
    keys = data.get("data") or []
    print(f"✓ ({len(keys)} existing)")
    return keys


def create_api_key(session, api_key_name: str = "mimo-register") -> dict:
    """POST /api/v1/apiKeys — create new API key.

    Note: MiMo requires `api-platform_ph` as QUERY PARAM (not cookie) for
    this endpoint. Server-side validation likely checks both.
    """
    print(f"  [6/6] POST /api/v1/apiKeys (name={api_key_name!r})…", end=" ")

    # Extract api-platform_ph from session cookies
    jar = getattr(session.cookies, "jar", session.cookies)
    ph = None
    for c in jar:
        if c.name == "api-platform_ph":
            ph = c.value
            break

    if not ph:
        raise RuntimeError("no api-platform_ph in session cookies (SSO may have failed)")

    url = f"{MIMO_BASE}/api/v1/apiKeys?api-platform_ph={quote(ph)}"
    r = session.post(
        url,
        json={"apiKeyName": api_key_name},
        headers={
            "Content-Type": "application/json",
            "Origin": MIMO_BASE,
            "Referer": f"{MIMO_BASE}/console/profile",
        },
        impersonate="chrome124",
    )

    if r.status_code != 200:
        raise RuntimeError(f"create apiKey failed: {r.status_code} {r.text[:300]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"create apiKey error code={data.get('code')}: {data.get('message', data)}")

    key_data = data["data"]
    print(f"✓ id={key_data.get('id')} key={key_data.get('apiKey', '')[:25]}…")

    # Save to file (mode 0o600 — owner read/write only)
    out_path = Path("mimo_api_key.json")
    out_path.write_text(json.dumps({
        "id": key_data.get("id"),                       # API key database ID
        "apiKeyName": key_data.get("apiKeyName"),
        "apiKey": key_data.get("apiKey"),               # full secret (handle with care!)
        "redactedApiKey": key_data.get("redactedApiKey"),
        "createTime": key_data.get("createTime"),
    }, indent=2))
    try:
        out_path.chmod(0o600)
    except Exception:
        pass
    print(f"  [saved] {out_path} (chmod 600)")
    return key_data


def run(email: str = None, password: str = None,
        account_file: str = None, account_row: int = 0,
        api_key_name: str = None, list_keys: bool = False,
        create_key: bool = True) -> dict:
    """End-to-end: register / use existing → SSO → MiMo profile → API key.

    Args:
        email: kalau diisi, register akun baru dengan email ini.
               Kalau None, generate dari `EMAIL_DOMAIN` env.
        password: kalau diisi, password untuk register.
                  Kalau None, baca dari `XIAOMI_PASSWORD` env (atau random).
        account_file: kalau diisi, pakai akun dari JSON/JSONL file (skip register)
        account_row: row index di file
        api_key_name: kalau diisi, create API key dengan nama ini.
                      Kalau None, baca dari `API_KEY_NAME` env.
        list_keys: kalau True, list existing API keys (skip create)
        create_key: kalau False, skip API key creation sama sekali
                    (default True supaya full pipeline otomatis)

    Returns dict dengan {email, cookies, profile, api_key?, status}
    """
    # ── Resolve defaults dari env ─────────────────────────────────────
    if email is None and account_file is None:
        email = _generate_email()
        if email:
            print(f"[auto-email] {email}  (dari EMAIL_DOMAIN env)")
        else:
            raise RuntimeError(
                "tidak ada --email/--account, dan EMAIL_DOMAIN env tidak diset. "
                "Set EMAIL_DOMAIN=mimo.domain-anda.com di .env untuk auto-generate, "
                "atau pakai --email X / --account FILE"
            )
    if password is None and account_file is None:
        password = _default_password()
        env_pw = os.getenv("XIAOMI_PASSWORD", "")
        if env_pw:
            print(f"[auto-password] dari XIAOMI_PASSWORD env")
        else:
            print(f"[auto-password] generated random (tidak ada di env)")
    if api_key_name is None and not list_keys and create_key:
        api_key_name = os.getenv("API_KEY_NAME", "").strip() or "mimo-register"
        print(f"[auto-api-key-name] {api_key_name}")

    print("=" * 60)
    print(f"E2E: register → SSO → MiMo profile (→ API key?)")
    print("=" * 60)

    # ── Step 1: Register atau pakai existing ─────────────────────────────
    account = None
    if account_file:
        account = load_account_file(account_file, account_row)
        email = account["email"]
        print(f"[1/6] Skip register — pakai existing: {email} (from {account_file})")
        if not account.get("cookies", {}).get("passToken"):
            print("  ⚠ cookies kosong / tidak ada passToken — register ulang")
            password = password or account.get("password")
            if not password:
                raise RuntimeError("no password saved in account record")
            account = register(email=email, password=password)
    elif email:
        password = password or None  # will use XIAOMI_PASSWORD env if None
        print(f"[1/6] Register akun baru: {email}")
        try:
            account = register(email=email, password=password)
        except RegisterError as e:
            print(f"  ✗ register failed: {e}", file=sys.stderr)
            raise

    else:
        raise RuntimeError("provide --email + --password, OR --account <file>")

    print(f"  ✓ cookies: {list(account['cookies'].keys())}")

    # ── Step 2: Login pakai existing cookies (skip captcha) ─────────────
    print(f"\n[2/6] Login pakai existing cookies")
    login_data = login_with_cookies(account)
    print(f"  ✓ session ready (email={login_data['email']})")

    # ── Step 3: SSO ke MiMo ─────────────────────────────────────────────
    print(f"\n[3/6] SSO ke platform.xiaomimimo.com")
    sso = sso_to_mimo(login_data)
    if not sso:
        raise RuntimeError("SSO failed (lihat output di atas untuk detail)")
    mimo_session = sso["session"]

    # ── Step 4: Load profile ────────────────────────────────────────────
    print(f"\n[4/6] Load MiMo profile")
    profile = load_profile(mimo_session)

    # ── Step 5 (optional): List / create API key ───────────────────────
    api_key_result = None
    if list_keys:
        print(f"\n[5/6] List existing API keys")
        keys = list_api_keys(mimo_session)
        api_key_result = {"existing": keys}
        for i, k in enumerate(keys, 1):
            print(f"  [{i}] {k.get('apiKeyName')}: id={k.get('id')} {k.get('redactedApiKey')}")
    elif api_key_name and create_key:
        # Verify agreement dulu — kalau belum true, skip create API key
        print(f"\n[5/6] Check agreement (prerequisite untuk API key)")
        agreed = check_agreement(mimo_session)
        if not agreed:
            print(f"  ⚠ SKIP create API key — agreement belum di-accept")
            print(f"     Login manual ke https://platform.xiaomimimo.com untuk accept")
            api_key_result = None
        else:
            print(f"\n[6/6] Create API key")
            api_key_result = create_api_key(mimo_session, api_key_name)
    else:
        if not create_key:
            print(f"\n[5/6] Skip API key creation (--no-api-key)")
        else:
            print(f"\n[5/6] Skip API key creation (no --api-key-name, --list-api-keys, atau API_KEY_NAME env)")

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✓✓✓ BERHASIL MASUK MiMo PLATFORM ✓✓✓")
    print("=" * 60)
    print(f"  Email        : {profile.get('email')}")
    print(f"  User ID      : {profile.get('userId')}")
    print(f"  Phone        : {profile.get('phone') or '(belum di-bind)'}")
    print(f"  Agreement    : {profile.get('agreement')}")
    print(f"  IDC          : {profile.get('idc')}")

    if api_key_result:
        if "apiKey" in api_key_result:
            print(f"  API Key      : {api_key_result['apiKey'][:35]}…")
            print(f"  API Key ID   : {api_key_result.get('id')}")
        else:
            print(f"  API Keys     : {len(api_key_result.get('existing', []))} existing")

    # ── Save to files ────────────────────────────────────────────────
    print()
    save_account_to_files(account, profile, api_key_result)

    print("=" * 60)
    print("  NOTE: bind_referral OFF — tidak apply UltraSpeed")
    print("=" * 60)

    return {
        "email": email,
        "cookies": account["cookies"],
        "profile": profile,
        "api_key": api_key_result,
        "status": "success",
    }


def main():
    ap = argparse.ArgumentParser(
        description="End-to-end: register → SSO → MiMo profile (→ API key). "
                    "Defaults dibaca dari env (EMAIL_DOMAIN, XIAOMI_PASSWORD, API_KEY_NAME).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  # Pakai defaults dari env (zero CLI args):
  python -m mimo.e2e

  # Custom email + default password/api-key:
  python -m mimo.e2e --email custom@domain.com

  # Pakai existing account + create API key:
  python -m mimo.e2e --account xiaomi_account.json --api-key-name "key-v2"

  # List existing API keys:
  python -m mimo.e2e --account xiaomi_account.json --list-api-keys

Env vars yang dipakai sebagai default:
  EMAIL_DOMAIN     → auto-generate email <random>@<domain>
  XIAOMI_PASSWORD  → password untuk register (kalau tidak di-override)
  API_KEY_NAME     → nama API key yang di-create
        """,
    )
    ap.add_argument("--email",
                    help="email untuk register baru (default: auto-generate dari EMAIL_DOMAIN env)")
    ap.add_argument("--password",
                    help="password untuk register baru (default: dari XIAOMI_PASSWORD env)")
    ap.add_argument("--account",
                    help="JSON/JSONL file (skip register, pakai existing)")
    ap.add_argument("--row", type=int, default=0, help="row index di file (default 0)")
    ap.add_argument("--api-key-name", default=None,
                    help="nama API key (default: dari API_KEY_NAME env, atau 'mimo-register')")
    ap.add_argument("--list-api-keys", action="store_true",
                    help="list existing API keys (skip create)")
    ap.add_argument("--no-api-key", action="store_true",
                    help="skip API key creation sama sekali")
    args = ap.parse_args()

    # Validate: minimal satu mode (account atau email)
    if not args.account and not args.email:
        # Boleh: pakai env defaults kalau EMAIL_DOMAIN diset
        if not os.getenv("EMAIL_DOMAIN", "").strip():
            ap.error("provide --email, --account, OR set EMAIL_DOMAIN env")

    try:
        run(email=args.email, password=args.password,
            account_file=args.account, account_row=args.row,
            api_key_name=args.api_key_name,
            list_keys=args.list_api_keys,
            create_key=not args.no_api_key)
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()