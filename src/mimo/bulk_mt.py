"""Bulk register + SSO + API key untuk banyak akun — MULTI-THREADED.

Perbedaan dari `bulk.py`:
  - bulk.py    : N akun, sequential (satu-satu, ada delay antar akun)
  - bulk_mt.py : N akun, paralel via ThreadPoolExecutor, dengan lock untuk file I/O

Mengimpor dari module existing (bulk.py, e2e.py) — tidak duplicate logic.

Karena work bersifat I/O-bound (network calls), threading memberi speedup besar.
Lock dipakai hanya untuk shared file I/O (xiaomi_account.json, accounts.txt).

Proxy configuration (via env atau CLI):
  - env PROXY_URL=http://user:pass@host:port  (single proxy — DataImpulse format)
  - CLI --proxy URL                          (override env)
  - env USE_PROXY=0                          (disable proxy total)

Usage:
    # Pakai env PROXY_URL
    python -m mimo.bulk_mt --count 5 --email-domain example.com

    # 10 akun paralel dengan 4 workers
    python -m mimo.bulk_mt --count 10 --workers 4 --email-domain example.com

    # CLI proxy override
    python -m mimo.bulk_mt --count 3 --email-domain example.com \\
        --proxy http://user:pass@gw.dataimpulse.com:823

    # Disable proxy (env USE_PROXY=0)
    USE_PROXY=0 python -m mimo.bulk_mt --count 3 --email-domain example.com

    # Dry run
    python -m mimo.bulk_mt --count 5 --email-domain example.com --dry-run
"""

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .bulk import (
    process_one,
    generate_email,
    generate_password,
    load_existing_emails,
    load_jsonl_emails,
    utcnow_iso,
)
from .e2e import save_account_to_files
from .register import check_proxy
from ._ansi import C

load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# Thread-safe primitives
# ═══════════════════════════════════════════════════════════════════════════════

_print_lock = threading.Lock()
_save_lock = threading.Lock()


def tprint(msg: str) -> None:
    """Thread-safe print — serialize stdout supaya output tidak tumpang tindih."""
    with _print_lock:
        print(msg, flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Color & trim helpers (no-op kalau bukan TTY)
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


def _short(s, maxlen: int = 100) -> str:
    """Trim string untuk log: ambil baris pertama, potong di maxlen + ellipsis."""
    if s is None:
        return ""
    s = str(s)
    s = s.split("\n", 1)[0].strip()
    if len(s) > maxlen:
        return s[:maxlen].rstrip() + "…"
    return s


def _short_proxy(url: str | None) -> str:
    """Mask user:pass di proxy URL untuk logging aman."""
    if not url:
        return ""
    # Ganti user:pass@ jadi ***@ supaya credential tidak bocor di log
    import re as _re
    return _re.sub(r"(://)[^@/]+@", r"\1***@", url)


# ═══════════════════════════════════════════════════════════════════════════════
# Thread-safe wrapper untuk save_account_to_files
# ═══════════════════════════════════════════════════════════════════════════════

_save_original = save_account_to_files


def _save_account_thread_safe(account: dict, profile: dict, api_key_data: dict = None) -> None:
    """Wrap save_account_to_files dengan lock — RMW ke shared files harus serial."""
    with _save_lock:
        _save_original(account, profile, api_key_data)


# Monkey-patch supaya process_one() dari bulk.py pakai versi thread-safe
# (bulk.py import e2e.save_account_to_files by reference, jadi patch di sini cukup
#  karena process_one akan lookup name `save_account_to_files` di module bulk
#  yang sudah kita re-export, BUKAN dari e2e langsung — jadi kita patch di bulk)
import mimo.bulk as _bulk_mod
_bulk_mod.save_account_to_files = _save_account_thread_safe


# ═══════════════════════════════════════════════════════════════════════════════
# Worker wrapper
# ═══════════════════════════════════════════════════════════════════════════════

def _worker(idx: int, total: int, email: str, password: str, api_key_name: str,
            *, dry_run: bool = False,
            proxy: str | None = None,
            max_retries: int = 5) -> dict:
    """Run process_one() dengan retry pada proxy yang sama.

    Logic per akun:
      - attempt 1..max_retries (5x default):
        - panggil process_one(proxy=...)
        - kalau success → return
        - kalau fail → tunggu 2s → retry
      - Kalau max_retries habis → return last failed result
    """
    tag = _c("cyan", f"[{idx}/{total}]")
    proxy_tag = _c("dim", f" via {_short_proxy(proxy)}") if proxy else ""

    tprint(f"\n{_c('dim', '=' * 60)}")
    tprint(f"{tag} {_c('bold', 'START')} {email}{proxy_tag}")
    tprint(f"{_c('dim', '=' * 60)}")

    t0 = time.time()
    last_result: dict = {}

    for attempt in range(1, max_retries + 1):
        result = process_one(email, password, api_key_name,
                             dry_run=dry_run, proxy=proxy)
        status = result.get("status", "unknown")

        if status == "success":
            elapsed = time.time() - t0
            tprint(f"{tag} {_c('green', '✓ SUCCESS')} {email} "
                   f"{_c('dim', f'({elapsed:.1f}s, attempt {attempt})')}")
            return result

        if status == "dry_run":
            tprint(f"{tag} {_c('yellow', '~ DRY RUN')} {email}")
            return result

        last_result = result
        err = _short(result.get("error", "unknown"), maxlen=100)

        if attempt < max_retries:
            wait = 2
            tprint(f"{tag} {_c('yellow', f'⟳ retry {attempt + 1}/{max_retries}')} "
                   f"in {wait}s — {err}")
            time.sleep(wait)
        else:
            tprint(f"{tag} {_c('red', f'✗ {max_retries}× failed')} — {err}")

    elapsed = time.time() - t0
    err = _short(last_result.get("error", "unknown"), maxlen=120) if last_result else "unknown"
    tprint(f"{tag} {_c('red', '✗ FAILED')} {email} "
           f"{_c('dim', f'({elapsed:.1f}s, {max_retries} attempts)')} — {err}")
    return last_result


# ═══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def run(
    count: int = 1,
    workers: int = 3,
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
    """Run bulk processing (multi-threaded).

    Args:
        count: jumlah akun (default 1)
        workers: jumlah concurrent workers (default 3)
        email_domain: domain untuk generate email (default: dari EMAIL_DOMAIN env)
        api_key_name: nama API key (default: dari API_KEY_NAME env, atau "bulk-key")
        password: password untuk semua akun (default: XIAOMI_PASSWORD env, atau random per akun)
        from_jsonl: path ke JSONL file dengan email list (override generate)
        out_path: output JSON file (default: xiaomi_account.json)
        dry_run: cetak plan tanpa eksekusi
        proxy: single proxy URL override (prioritas tertinggi). Default: env PROXY_URL.
        max_retries: max retry dengan proxy yang sama (default 5)

    Returns: list of result dicts (satu per akun, urutan不一定 sesuai input)
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
        tprint(_c("dim", "[proxy] USE_PROXY=0 — disabled by env"))
    elif not proxy:
        tprint(_c("yellow", "[proxy] PROXY_URL env not set — multi-threading bisa kena IP-block"))
    else:
        #  tprint(f"[proxy] ({proxy_src}): {_short_proxy(proxy)}")
        tprint('\n')

    # ── Proxy health check (skip kalau PROXY_CHECK=0) ──────────────────
    if proxy and os.getenv("PROXY_CHECK", "1").strip() != "0":
        # tprint(f"[proxy-check] verifying {_short_proxy(proxy)}…")
        ok = check_proxy(proxy)
        if not ok:
            tprint(_c("yellow", "[proxy-check] proxy mungkin mati — "
                                 "lanjutkan dengan risiko gagal"))

    # ── Build email list ──────────────────────────────────────────────
    if from_jsonl:
        emails = load_jsonl_emails(from_jsonl)
        if not emails:
            tprint(_c("red", f"[FAIL] no valid emails in {from_jsonl}"))
            return []
        tprint(f"[bulk_mt] loaded {len(emails)} emails dari {from_jsonl}")
    elif email_domain:
        emails = [generate_email(email_domain) for _ in range(count)]
        # tprint(f"[bulk_mt] generate {count} emails di {email_domain}")
    else:
        tprint(_c("red", "[FAIL] provide --count + --email-domain, OR --from-jsonl, "
                          "OR set EMAIL_DOMAIN env"))
        return []

    # ── Resume: skip emails yang sudah ada di output file ──────────────
    out_p = Path(out_path)
    already = load_existing_emails(out_p)
    if already:
        before = len(emails)
        emails = [e for e in emails if e not in already]
        # tprint(f"[resume] skip {before - len(emails)} akun sudah di {out_path} (sudah ada API key)")
    if not emails:
        tprint("[bulk_mt] no new emails to process — done!")
        return []

    # ── Password resolution ───────────────────────────────────────────
    use_random_password = not password
    if not password:
        tprint("[bulk_mt] no XIAOMI_PASSWORD env — akan generate random password per akun")

    # ── Plan ───────────────────────────────────────────────────────────
    workers = max(1, min(workers, len(emails)))
    print()
    print(_c("cyan", "=" * 60))
    print(_c("bold", f"BULK REGISTER (MULTI-THREADED): "
                      f"{len(emails)} akun, {workers} workers"))
    print(_c("cyan", "=" * 60))
    print(f"  Email domain    : {email_domain}")
    print(f"  API key name    : {api_key_name}")
    print(f"  Password        : {'random per akun' if use_random_password else 'XIAOMI_PASSWORD env'}")
    print(f"  Workers         : {workers} concurrent threads")
    if proxy:
        print(f"  Proxy           : {_short_proxy(proxy)}  ({proxy_src})")
    else:
        print(f"  Proxy           : {_c('yellow', '(none)')} — bisa kena IP-block")
    print(f"  Retry policy    : {max_retries} attempts/akun")
    print(f"  Output          : {out_path}")
    print(f"  Resume mode     : skip kalau sudah ada API key")
    print(f"  File I/O lock   : {_c('green', 'aktif')} (save_account_to_files serialized)")
    print(_c("cyan", "=" * 60))
    if dry_run:
        print("\n[DRY RUN] Sample emails:")
        for e in emails[:5]:
            print(f"  {e}")
        if len(emails) > 5:
            print(f"  ... ({len(emails) - 5} more)")
        return []

    # ── Prepare per-account passwords ─────────────────────────────────
    # Pre-generate supaya setiap worker dapat password unik (kalau random)
    passwords = [generate_password() if use_random_password else password
                 for _ in emails]

    # ── Dispatch workers ──────────────────────────────────────────────
    results: list[dict] = []
    success = failed = 0
    total = len(emails)
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Pass single proxy ke semua worker. Worker yang manage retry count.
        futures = {
            pool.submit(_worker, i + 1, total, email, pw, api_key_name,
                        dry_run=dry_run, proxy=proxy,
                        max_retries=max_retries): email
            for i, (email, pw) in enumerate(zip(emails, passwords))
        }

        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as e:
                email = futures[fut]
                result = {
                    "email":       email,
                    "status":      "failed",
                    "error":       f"Worker exception: {type(e).__name__}: {e}",
                    "started_at":  utcnow_iso(),
                    "finished_at": utcnow_iso(),
                }
                tprint(f"  ✗ worker exception for {email}: {e}")

            results.append(result)
            if result["status"] == "success":
                success += 1
            else:
                failed += 1

    elapsed_total = time.time() - t_start

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print(_c("cyan", "=" * 60))
    print(_c("bold", "BULK MT SUMMARY"))
    print(_c("cyan", "=" * 60))
    print(f"  Total   : {total}")
    print(f"  Success : {_c('green', str(success))}")
    print(f"  Failed  : {_c('red' if failed else 'dim', str(failed))}")
    print(f"  Workers : {workers}")
    print(f"  Elapsed : {elapsed_total:.1f}s "
          f"{_c('dim', f'(avg {elapsed_total / max(total, 1):.1f}s/akun)')}")
    print(f"  Output  : {out_path}")
    print(_c("cyan", "=" * 60))

    # Save bulk run log (append, dengan lock)
    log_path = Path("bulk_mt_run.log.jsonl")
    with _save_lock:
        with log_path.open("a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    tprint(f"  Run log: {log_path.absolute()}")
    print("=" * 60)

    return results


def main():
    ap = argparse.ArgumentParser(
        description="Bulk register + SSO + API key untuk banyak akun "
                    "(MULTI-THREADED). Defaults dari env "
                    "(EMAIL_DOMAIN, XIAOMI_PASSWORD, API_KEY_NAME).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  # 5 akun paralel dengan 3 workers
  python -m mimo.bulk_mt --count 5 --workers 3 --email-domain example.com

  # 10 akun, 4 workers, dari JSONL
  python -m mimo.bulk_mt --from-jsonl accounts.jsonl --workers 4

  # Dry run
  python -m mimo.bulk_mt --count 5 --email-domain example.com --dry-run
        """,
    )
    ap.add_argument("--count", type=int, default=1,
                    help="jumlah akun (default 1)")
    ap.add_argument("--workers", type=int, default=3,
                    help="jumlah concurrent workers/threads (default 3)")
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
        workers=args.workers,
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
