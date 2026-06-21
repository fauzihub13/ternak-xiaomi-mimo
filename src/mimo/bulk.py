"""Bulk register + SSO + API key untuk banyak akun sequential.

Perbedaan dari `e2e.py` (single akun):
  - e2e.py : 1 akun, full pipeline
  - bulk.py: N akun, sequential, dengan rate limiting + resume

Proxy configuration (via env atau CLI, sama seperti bulk_mt):
  - env PROXY_URL=http://user:pass@host:port  (single proxy — DataImpulse format)
  - CLI --proxy URL                          (override env)
  - env USE_PROXY=0                          (disable proxy total)
  - env PROXY_CHECK=0                        (skip proxy health check)

Usage:
    # Pakai env PROXY_URL
    python -m mimo.bulk --count 5 --email-domain example.com

    # Custom count + delay
    python -m mimo.bulk --count 5 --delay-min 300 --delay-max 900

    # Custom email strategy
    python -m mimo.bulk --count 10 --email-domain other-domain.com

    # Process existing JSONL file
    python -m mimo.bulk --from-jsonl accounts.jsonl

    # CLI proxy override
    python -m mimo.bulk --count 3 --email-domain example.com \\
        --proxy http://user:pass@gw.dataimpulse.com:823

    # Dry run
    python -m mimo.bulk --count 3 --dry-run
"""

import argparse
import json
import os
import random
import re
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from .register import register, RegisterError, check_proxy
from .bot import login_with_cookies, sso_to_mimo, MIMO_BASE
from ._ansi import C
from .e2e import save_account_to_files, check_agreement
from curl_cffi import requests as cffi_requests

load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# Color helper (TTY-aware — no-op kalau stdout di-pipe)
# ═══════════════════════════════════════════════════════════════════════════════

_USE_COLOR = sys.stdout.isatty()
_PALETTE = {
    "reset":  C.RESET,
    "bold":   C.BOLD,
    "dim":    C.DIM,
    "gray":   C.GRAY,
    "red":    C.RED,
    "green":  C.GREEN,
    "yellow": C.YELLOW,
    "cyan":   C.CYAN,
}


def _c(name: str, s: str) -> str:
    """Wrap string dengan ANSI color, no-op kalau stdout bukan TTY."""
    if not _USE_COLOR:
        return s
    return f"{_PALETTE[name]}{s}{C.RESET}"


def _short_proxy(url: str | None) -> str:
    """Mask user:pass di proxy URL untuk logging aman."""
    if not url:
        return ""
    return re.sub(r"(://)[^@/]+@", r"\1***@", url)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_email(domain: str) -> str:
    """Random catch-all email: <10-char>@<domain>"""
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{local}@{domain}"


def generate_password(length: int = 18) -> str:
    """Random strong password: upper + lower + digit + special."""
    while True:
        pw = "".join(random.choices(
            string.ascii_letters + string.digits + "!@#$%^&*",
            k=length,
        ))
        if (any(c.isupper() for c in pw)
                and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$%^&*" for c in pw)):
            return pw


def load_existing_emails(out_path: Path) -> set[str]:
    """Read xiaomi_account.json — return set of emails yang sudah success."""
    if not out_path.exists():
        return set()
    try:
        data = json.loads(out_path.read_text())
        if not isinstance(data, list):
            return set()
        return {r["email"] for r in data if r.get("email") and r.get("api_key")}
    except json.JSONDecodeError:
        return set()


def load_jsonl_emails(path: str | Path) -> list[str]:
    """Read JSONL file (output batch) — return list of email values per row."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    emails = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("status") == "success" and obj.get("email"):
                emails.append(obj["email"])
        except json.JSONDecodeError:
            pass
    return emails


# ═══════════════════════════════════════════════════════════════════════════════
# Core: process 1 akun (register → SSO → agreement → API key → save)
# ═══════════════════════════════════════════════════════════════════════════════

def process_one(
    email: str,
    password: str,
    api_key_name: str,
    *,
    dry_run: bool = False,
    proxy: str | None = None,
) -> dict:
    """Full pipeline untuk 1 akun. Return dict dengan status + artifacts.

    Args:
        proxy: Proxy URL untuk register step (per-worker rotation). None = env.
               Catatan: login/SSO/MiMo pakai env PROXY_URL (backward compat).
    """
    result = {
        "email":    email,
        "status":   "pending",
        "error":    None,
        "started_at":  utcnow_iso(),
        "finished_at": None,
    }
    if dry_run:
        result["status"] = "dry_run"
        return result

    try:
        # Step 1: Register (pakai proxy kalau ada)
        print(f"\n{_c('cyan', '[REGISTER]')} {email}")
        account = register(email=email, password=password, proxy=proxy)
        print(f"  {_c('green', '✓')} registered, cookies: {list(account['cookies'].keys())}")

        # Step 2: Login pakai existing cookies
        print(f"{_c('cyan', '[LOGIN]')} {email}")
        login_data = login_with_cookies(account)

        # Step 3: SSO ke MiMo
        print(f"{_c('cyan', '[SSO]')} {email}")
        sso = sso_to_mimo(login_data)
        if not sso:
            raise RuntimeError("SSO failed")
        mimo_session = sso["session"]

        # Step 4: Load profile
        print(f"{_c('cyan', '[PROFILE]')} {email}")
        r = mimo_session.get(f"{MIMO_BASE}/api/v1/userProfile", impersonate="chrome124")
        if r.status_code != 200:
            raise RuntimeError(f"profile failed: {r.status_code}")
        profile = r.json().get("data", {})

        # Step 5: Check agreement
        print(f"{_c('cyan', '[AGREEMENT]')} {email}")
        agreed = check_agreement(mimo_session)
        if not agreed:
            raise RuntimeError("agreement belum di-accept — skip API key")

        # Step 6: Create API key
        print(f"{_c('cyan', '[APIKEY]')} {email} (name={api_key_name!r})")
        jar = getattr(mimo_session.cookies, "jar", mimo_session.cookies)
        ph = next((c.value for c in jar if c.name == "api-platform_ph"), None)
        if not ph:
            raise RuntimeError("no api-platform_ph")
        url = f"{MIMO_BASE}/api/v1/apiKeys?api-platform_ph={quote(ph)}"
        r = mimo_session.post(
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
            raise RuntimeError(f"create apiKey failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"create apiKey error: {data}")
        api_key_data = data["data"]
        print(f"  {_c('green', '✓')} api key id={api_key_data['id']} {api_key_data['apiKey'][:25]}…")

        # Step 7: Save to files
        save_account_to_files(account, profile, api_key_data)

        result.update({
            "status":      "success",
            "userId":      profile.get("userId"),
            "api_key_id":  api_key_data.get("id"),
            "finished_at": utcnow_iso(),
        })
        return result

    except (RegisterError, RuntimeError, Exception) as e:
        result.update({
            "status":      "failed",
            "error":       f"{type(e).__name__}: {e}",
            "finished_at": utcnow_iso(),
        })
        print(f"  {_c('red', '✗ FAILED')}: {type(e).__name__}: {e}")
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def run(
    count: int = 1,
    delay_min: int = 3,
    delay_max: int = 10,
    *,
    email_domain: str = None,
    api_key_name: str = None,
    password: str = None,
    from_jsonl: str = None,
    out_path: str = "xiaomi_account.json",
    dry_run: bool = False,
    proxy: str | None = None,
    max_retries: int = 5,
) -> list[dict]:
    """Run bulk processing.

    Args:
        count: jumlah akun (default 1)
        delay_min: delay minimum antar akun (detik, default 300)
        delay_max: delay maksimum antar akun (detik, default 900)
        email_domain: domain untuk generate email (default: dari EMAIL_DOMAIN env)
        api_key_name: nama API key (default: dari API_KEY_NAME env, atau "bulk-key")
        password: password untuk semua akun (default: dari XIAOMI_PASSWORD env, atau random)
        from_jsonl: path ke JSONL file dengan email list (override generate)
        out_path: output JSON file (default: xiaomi_account.json)
        dry_run: cetak plan tanpa eksekusi
        proxy: single proxy URL override (default: env PROXY_URL)
        max_retries: max retry per akun dengan proxy sama sebelum mark failed

    Returns: list of result dicts (satu per akun)
    """
    # ── Resolve defaults ──────────────────────────────────────────────
    if email_domain is None:
        email_domain = os.getenv("EMAIL_DOMAIN", "").strip()
    if api_key_name is None:
        api_key_name = os.getenv("API_KEY_NAME", "").strip() or "bulk-key"
    if password is None:
        password = os.getenv("XIAOMI_PASSWORD", "").strip()

    # ── Resolve proxy (CLI > env > none) ──────────────────────────────
    # Env vars:
    #   USE_PROXY=0   → disable proxy (overrides semua)
    #   PROXY_URL     → single proxy URL (cth: http://user:pass@host:port)
    use_proxy_env = os.getenv("USE_PROXY", "1").strip() != "0"
    if proxy is None:
        proxy = os.getenv("PROXY_URL", "").strip() or None
    proxy_src = "CLI --proxy" if proxy and "--proxy" in sys.argv else \
                ("env PROXY_URL" if proxy else "none")
    if not use_proxy_env:
        proxy = None
        print(_c("dim", "[proxy] USE_PROXY=0 — disabled by env"))
    elif not proxy:
        print(_c("yellow", "[proxy] none — register bisa kena IP-block"))
    else:
        # print(f"[proxy] ({proxy_src}): {_short_proxy(proxy)}")
        print('\n')
        

    # ── Proxy health check (skip kalau PROXY_CHECK=0) ──────────────────
    if proxy and os.getenv("PROXY_CHECK", "1").strip() != "0":
        # print(f"[proxy-check] verifying {_short_proxy(proxy)}…")
        ok = check_proxy(proxy)
        if not ok:
            print(_c("yellow", "[proxy-check] proxy mungkin mati — "
                                "lanjutkan dengan risiko gagal"))

    # ── Build email list ──────────────────────────────────────────────
    if from_jsonl:
        emails = load_jsonl_emails(from_jsonl)
        if not emails:
            print(_c("red", f"[FAIL] no valid emails in {from_jsonl}"))
            return []
        print(f"[bulk] loaded {len(emails)} emails dari {from_jsonl}")
    elif email_domain:
        emails = [generate_email(email_domain) for _ in range(count)]
        # print(f"[bulk] generate {count} emails di {email_domain}")
    else:
        print(_c("red", "[FAIL] provide --count + --email-domain, OR --from-jsonl, "
                          "OR set EMAIL_DOMAIN env"))
        return []

    # ── Resume: skip emails yang sudah ada di output file ──────────────
    out_p = Path(out_path)
    already = load_existing_emails(out_p)
    if already:
        before = len(emails)
        emails = [e for e in emails if e not in already]
        # print(_c("dim", f"[resume] skip {before - len(emails)} akun sudah di {out_path} (sudah ada API key)"))
    if not emails:
        print("[bulk] no new emails to process — done!")
        return []

    # ── Password resolution ───────────────────────────────────────────
    use_random_password = not password
    if not password:
        print(f"[bulk] no XIAOMI_PASSWORD env — akan generate random password per akun")

    # ── Plan ───────────────────────────────────────────────────────────
    print()
    print(_c("cyan", "=" * 60))
    print(_c("bold", f"BULK REGISTER: {len(emails)} akun sequential"))
    print(_c("cyan", "=" * 60))
    print(f"  Email domain    : {email_domain}")
    print(f"  API key name    : {api_key_name}")
    print(f"  Password        : {'random per akun' if use_random_password else 'XIAOMI_PASSWORD env'}")
    print(f"  Delay per akun  : {delay_min}-{delay_max}s ({delay_min // 60}-{delay_max // 60} min)")
    if proxy:
        print(f"  Proxy           : {_short_proxy(proxy)}  ({proxy_src})")
    else:
        print(f"  Proxy           : {_c('yellow', '(none)')} — bisa kena IP-block")
    print(f"  Retry policy    : {max_retries} attempts/akun")
    print(f"  Output          : {out_path}")
    print(f"  Resume mode     : skip kalau sudah ada API key")
    print(f"  Estimate time   : ~{(len(emails) * (delay_min + delay_max) // 2) // 60} min")
    print(_c("cyan", "=" * 60))
    if dry_run:
        print(f"\n{_c('yellow', '[DRY RUN]')} Sample emails:")
        for e in emails[:5]:
            print(f"  {e}")
        if len(emails) > 5:
            print(f"  ... ({len(emails) - 5} more)")
        return []

    # ── Process sequentially ──────────────────────────────────────────
    results = []
    success = failed = 0
    total = len(emails)
    for i, email in enumerate(emails, 1):
        proxy_tag = _c("dim", f" via {_short_proxy(proxy)}") if proxy else ""
        print(f"\n{_c('dim', '=' * 60)}")
        print(f"{_c('cyan', f'[{i}/{total}]')} PROCESSING {email}{proxy_tag}")
        print(f"{_c('dim', '=' * 60)}")

        # Random password per akun kalau env kosong
        this_password = generate_password() if use_random_password else password

        # Retry loop (5x default dengan proxy sama)
        result = None
        for attempt in range(1, max_retries + 1):
            result = process_one(email, this_password, api_key_name, proxy=proxy)
            status = result.get("status", "unknown")

            if status == "success":
                print(_c("green", f"  ✓ SUCCESS {email} (attempt {attempt})"))
                break

            err = result.get("error", "unknown")
            err_short = re.sub(r"\s+", " ", str(err))[:100]
            if attempt < max_retries:
                wait = 2
                print(_c("yellow", f"  ⟳ retry {attempt + 1}/{max_retries} in {wait}s — {err_short}"))
                time.sleep(wait)
            else:
                print(_c("red", f"  ✗ {max_retries}× failed — {err_short}"))

        results.append(result)

        if result["status"] == "success":
            success += 1
        else:
            failed += 1

        # Delay ke akun berikutnya (kalau bukan yang terakhir)
        if i < total:
            delay = random.randint(delay_min, delay_max)
            print(_c("dim", f"\n[sleep] {delay}s → next akun..."))
            time.sleep(delay)

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print(_c("cyan", "=" * 60))
    print(_c("bold", "BULK SUMMARY"))
    print(_c("cyan", "=" * 60))
    print(f"  Total  : {total}")
    print(f"  Success: {_c('green', str(success))}")
    print(f"  Failed : {_c('red' if failed else 'dim', str(failed))}")
    print(f"  Output : {out_path}")
    print(_c("cyan", "=" * 60))

    # Save bulk run log (separate dari xiaomi_account.json)
    log_path = Path("bulk_run.log.jsonl")
    with log_path.open("a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(_c("dim", f"  Run log: {log_path.absolute()}"))
    print(_c("cyan", "=" * 60))

    return results


def main():
    ap = argparse.ArgumentParser(
        description="Bulk register + SSO + API key untuk banyak akun sequential. "
                    "Defaults dari env (EMAIL_DOMAIN, XIAOMI_PASSWORD, API_KEY_NAME).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  # Zero args — pakai env defaults
  python -m mimo.bulk

  # 5 akun dengan custom delay
  python -m mimo.bulk --count 5 --delay-min 300 --delay-max 900

  # Custom domain
  python -m mimo.bulk --count 10 --email-domain other-domain.com

  # Process existing JSONL file
  python -m mimo.bulk --from-jsonl accounts.jsonl

  # Dry run — lihat plan tanpa eksekusi
  python -m mimo.bulk --count 3 --dry-run
        """,
    )
    ap.add_argument("--count", type=int, default=1,
                    help="jumlah akun (default 1)")
    ap.add_argument("--delay-min", type=int, default=3,
                    help="delay minimum antar akun, detik (default 300 = 5min)")
    ap.add_argument("--delay-max", type=int, default=10,
                    help="delay maksimum antar akun, detik (default 900 = 15min)")
    ap.add_argument("--email-domain", default=None,
                    help="domain untuk generate email (default: dari EMAIL_DOMAIN env)")
    ap.add_argument("--api-key-name", default=None,
                    help="nama API key (default: dari API_KEY_NAME env, atau 'bulk-key')")
    ap.add_argument("--password", default=None,
                    help="password untuk semua akun (default: XIAOMI_PASSWORD env, atau random per akun)")
    ap.add_argument("--from-jsonl", default=None,
                    help="path ke JSONL file dengan email list (override generate)")
    ap.add_argument("--out", default="xiaomi_account.json",
                    help="output JSON file (default: xiaomi_account.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="cetak plan tanpa eksekusi")
    ap.add_argument("--proxy", default=None,
                    help="single proxy URL override (cth: http://user:pass@gw.dataimpulse.com:823). "
                         "Default: env PROXY_URL. Set USE_PROXY=0 untuk disable.")
    ap.add_argument("--retries", type=int, default=5,
                    help="max retry per akun sebelum mark failed (default 5)")
    args = ap.parse_args()

    # Validate: minimal salah satu mode
    if not args.from_jsonl and not args.email_domain and not os.getenv("EMAIL_DOMAIN", "").strip():
        ap.error("provide --email-domain, --from-jsonl, OR set EMAIL_DOMAIN env")

    results = run(
        count=args.count,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        email_domain=args.email_domain,
        api_key_name=args.api_key_name,
        password=args.password,
        from_jsonl=args.from_jsonl,
        out_path=args.out,
        dry_run=args.dry_run,
        proxy=args.proxy,
        max_retries=args.retries,
    )

    # Exit code: 0 kalau semua success, 1 kalau ada failed
    if not results:
        sys.exit(1)
    if any(r["status"] not in ("success", "dry_run") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()