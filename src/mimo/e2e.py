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
    print("  [4/5] GET /api/v1/userProfile…", end=" ")
    r = session.get(f"{MIMO_BASE}/api/v1/userProfile", impersonate="chrome124")
    if r.status_code != 200:
        raise RuntimeError(f"profile failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"profile error code={data.get('code')}: {data.get('message', data)}")
    profile = data["data"]
    print(f"✓ userId={profile['userId']}")
    return profile


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
    print(f"  [5/5] POST /api/v1/apiKeys (name={api_key_name!r})…", end=" ")

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
        api_key_name: str = None, list_keys: bool = False) -> dict:
    """End-to-end: register / use existing → SSO → MiMo profile → API key.

    Args:
        email: kalau diisi, register akun baru dengan email ini
        password: password untuk register baru
        account_file: kalau diisi, pakai akun dari JSON/JSONL file (skip register)
        account_row: row index di file
        api_key_name: kalau diisi, create API key dengan nama ini setelah SSO
        list_keys: kalau True, list existing API keys (skip create)

    Returns dict dengan {email, cookies, profile, api_key?, status}
    """
    print("=" * 60)
    print(f"E2E: register → SSO → MiMo profile (→ API key?)")
    print("=" * 60)

    # ── Step 1: Register atau pakai existing ─────────────────────────────
    account = None
    if account_file:
        account = load_account_file(account_file, account_row)
        email = account["email"]
        print(f"[1/5] Skip register — pakai existing: {email} (from {account_file})")
        if not account.get("cookies", {}).get("passToken"):
            print("  ⚠ cookies kosong / tidak ada passToken — register ulang")
            password = password or account.get("password")
            if not password:
                raise RuntimeError("no password saved in account record")
            account = register(email=email, password=password)
            Path("xiaomi_account.json").write_text(json.dumps(account, indent=2))
            print(f"  [saved] xiaomi_account.json")
    elif email:
        password = password or None  # will use XIAOMI_PASSWORD env if None
        print(f"[1/5] Register akun baru: {email}")
        try:
            account = register(email=email, password=password)
        except RegisterError as e:
            print(f"  ✗ register failed: {e}", file=sys.stderr)
            raise
        Path("xiaomi_account.json").write_text(json.dumps(account, indent=2))
        print(f"  [saved] xiaomi_account.json")
    else:
        raise RuntimeError("provide --email + --password, OR --account <file>")

    print(f"  ✓ cookies: {list(account['cookies'].keys())}")

    # ── Step 2: Login pakai existing cookies (skip captcha) ─────────────
    print(f"\n[2/5] Login pakai existing cookies")
    login_data = login_with_cookies(account)
    print(f"  ✓ session ready (email={login_data['email']})")

    # ── Step 3: SSO ke MiMo ─────────────────────────────────────────────
    print(f"\n[3/5] SSO ke platform.xiaomimimo.com")
    sso = sso_to_mimo(login_data)
    if not sso:
        raise RuntimeError("SSO failed (lihat output di atas untuk detail)")
    mimo_session = sso["session"]

    # ── Step 4: Load profile ────────────────────────────────────────────
    print(f"\n[4/5] Load MiMo profile")
    profile = load_profile(mimo_session)

    # ── Step 5 (optional): List / create API key ───────────────────────
    api_key_result = None
    if list_keys:
        print(f"\n[5/5] List existing API keys")
        keys = list_api_keys(mimo_session)
        api_key_result = {"existing": keys}
        for i, k in enumerate(keys, 1):
            print(f"  [{i}] {k.get('apiKeyName')}: id={k.get('id')} {k.get('redactedApiKey')}")
    elif api_key_name:
        print(f"\n[5/5] Create API key")
        api_key_result = create_api_key(mimo_session, api_key_name)

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
        description="End-to-end: register → SSO → MiMo profile (→ API key)",
    )
    ap.add_argument("--email", help="email untuk register baru")
    ap.add_argument("--password", help="password untuk register baru")
    ap.add_argument("--account", help="JSON/JSONL file (output batch/register)")
    ap.add_argument("--row", type=int, default=0, help="row index di file")
    ap.add_argument("--api-key-name", default=None,
                    help="create API key dengan nama ini setelah SSO")
    ap.add_argument("--list-api-keys", action="store_true",
                    help="list existing API keys (skip create)")
    args = ap.parse_args()

    if not args.account and not args.email:
        ap.error("provide --email + --password, OR --account <file>")

    try:
        run(email=args.email, password=args.password,
            account_file=args.account, account_row=args.row,
            api_key_name=args.api_key_name,
            list_keys=args.list_api_keys)
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()