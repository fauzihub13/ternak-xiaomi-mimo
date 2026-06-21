"""Batch register beberapa akun Xiaomi dengan rate limiting.

Strategi aman untuk batch:
  - Sequential (1 akun pada satu waktu) — JANGAN parallel
  - Delay 5-15 menit antar akun (anti rate-limit Xiaomi)
  - Captcha retry up to 4× per akun
  - IMAP auto-filter by recipient address (sudah built-in di step7)
  - Output: JSONL (satu akun per baris) — append-only, aman di-interrupt
  - Resume-able: skip akun yang sudah terdaftar

Contoh penggunaan:
  # 5 akun dari catch-all domain
  python -m mimo.batch \\
    --count 5 \\
    --email-strategy catch_all \\
    --email-domain mimo.kamu.com \\
    --delay-min 5 --delay-max 15 \\
    --out accounts.jsonl

  # dari Gmail plus-alias
  python -m mimo.batch \\
    --count 3 \\
    --email-strategy gmail_plus \\
    --email-base akun.ku@gmail.com

  # dari file
  python -m mimo.batch \\
    --email-strategy from_file \\
    --email-file emails.txt
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .email_gen import generate_emails
from .register import register as register_one, RegisterError


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def already_registered(out_path: Path) -> set[str]:
    """Cek akun mana yang sudah pernah berhasil (untuk resume)."""
    done = set()
    if not out_path.exists():
        return done
    with out_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("status") == "success" and obj.get("email"):
                    done.add(obj["email"])
            except json.JSONDecodeError:
                continue
    return done


def build_email_list(args, env: dict) -> list[str]:
    """Bangun list email sesuai strategi."""
    if args.emails:
        return args.emails
    return generate_emails(
        count=args.count,
        strategy=args.email_strategy,
        base=args.email_base,
        domain=args.email_domain,
        prefix=args.email_prefix,
        file=args.email_file,
    )


def register_account(email: str, password: str, env: dict, attempt: int = 0) -> dict:
    """Override env, panggil register(), restore."""
    saved = {}
    for k in ("EMAIL", "XIAOMI_PASSWORD"):
        if k in os.environ:
            saved[k] = os.environ[k]
    os.environ["EMAIL"] = email
    os.environ["XIAOMI_PASSWORD"] = password
    try:
        result = register_one()
        return {
            "email": email,
            "password": password,
            "status": "success",
            "cookies": result.get("cookies", {}),
            "created_at": result.get("created_at", utcnow_iso()),
            "attempt": attempt + 1,
        }
    except (RegisterError, RuntimeError, TimeoutError) as e:
        return {
            "email": email,
            "password": password,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "failed_at": utcnow_iso(),
            "attempt": attempt + 1,
        }
    finally:
        for k, v in saved.items():
            os.environ[k] = v
        if k not in saved and k in os.environ:
            del os.environ[k]


def main():
    load_dotenv()

    ap = argparse.ArgumentParser(
        description="Batch register akun Xiaomi (sequential + rate-limit).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Email generation
    eg = ap.add_argument_group("Email generation")
    eg.add_argument("--count", type=int, default=1,
                   help="jumlah akun (default 1)")
    eg.add_argument("--emails", nargs="+",
                   help="list email eksplisit (override strategi)")
    eg.add_argument("--email-strategy", choices=["catch_all", "gmail_plus", "from_file"],
                   default=os.getenv("BATCH_EMAIL_STRATEGY", "catch_all"))
    eg.add_argument("--email-domain",
                   help="catch-all domain (untuk strategy=catch_all)")
    eg.add_argument("--email-base",
                   help="Gmail base (untuk strategy=gmail_plus)")
    eg.add_argument("--email-prefix",
                   help="optional prefix (untuk catch_all)")
    eg.add_argument("--email-file",
                   help="path ke file email list (untuk strategy=from_file)")

    # Password generation
    pg = ap.add_argument_group("Password")
    pg.add_argument("--password",
                   help="password tunggal untuk semua akun")
    pg.add_argument("--password-len", type=int, default=18,
                   help="panjang random password (default 18)")
    pg.add_argument("--password-from-file",
                   help="file dengan 1 password per baris (round-robin)")

    # Rate limiting
    rl = ap.add_argument_group("Rate limiting")
    rl.add_argument("--delay-min", type=int, default=300,
                   help="delay minimum antar akun (detik, default 300 = 5min)")
    rl.add_argument("--delay-max", type=int, default=900,
                   help="delay maksimum antar akun (detik, default 900 = 15min)")
    rl.add_argument("--max-retries", type=int, default=2,
                   help="retry per akun jika gagal (default 2)")
    rl.add_argument("--resume", action="store_true", default=True,
                   help="skip akun yang sudah success (default true)")
    rl.add_argument("--no-resume", dest="resume", action="store_false")

    # Output
    og = ap.add_argument_group("Output")
    og.add_argument("--out", default="accounts.jsonl",
                   help="output JSONL file (default accounts.jsonl)")
    og.add_argument("--dry-run", action="store_true",
                   help="cetak plan tanpa eksekusi")

    args = ap.parse_args()

    # Build email list
    try:
        emails = build_email_list(args, os.environ)
    except (ValueError, FileNotFoundError) as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)

    if not emails:
        print("[FAIL] Tidak ada email untuk diproses", file=sys.stderr)
        sys.exit(1)

    # Build password list
    passwords = []
    if args.password:
        passwords = [args.password] * len(emails)
    elif args.password_from_file:
        pfile = Path(args.password_from_file)
        passwords = [ln.strip() for ln in pfile.read_text().splitlines() if ln.strip()]
        if not passwords:
            print(f"[FAIL] {pfile} kosong", file=sys.stderr)
            sys.exit(1)
        # Round-robin jika kurang dari jumlah email
        while len(passwords) < len(emails):
            passwords.append(random.choice(passwords))
    else:
        # Generate random strong password
        for _ in emails:
            passwords.append(_gen_password(args.password_len))

    if len(emails) != len(passwords):
        print(f"[FAIL] email/password count mismatch: {len(emails)} vs {len(passwords)}",
              file=sys.stderr)
        sys.exit(1)

    # Resume: skip yang sudah ada
    out_path = Path(args.out)
    already = already_registered(out_path) if args.resume else set()
    pending = [(e, p) for e, p in zip(emails, passwords) if e not in already]

    # Plan summary
    print("=" * 60)
    print("MiMo Batch Register")
    print("=" * 60)
    print(f"Total emails      : {len(emails)}")
    print(f"Already registered: {len(already)}")
    print(f"Will process      : {len(pending)}")
    print(f"Delay per account : {args.delay_min}-{args.delay_max}s "
          f"({args.delay_min // 60}-{args.delay_max // 60} min)")
    print(f"Max retries       : {args.max_retries}")
    print(f"Output            : {out_path}")
    print(f"Resume mode       : {args.resume}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Sample emails:")
        for e, p in pending[:5]:
            print(f"  {e}  pw={p[:4]}***")
        if len(pending) > 5:
            print(f"  ... ({len(pending) - 5} more)")
        return

    # Process sequential
    success = 0
    failed = 0
    for i, (email, password) in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] Processing {email}")
        print(f"  password: {password[:4]}{'*' * (len(password) - 4)}")

        result = None
        for attempt in range(args.max_retries + 1):
            if attempt > 0:
                wait = random.randint(60, 180)
                print(f"  retry {attempt}/{args.max_retries} setelah {wait}s...")
                time.sleep(wait)

            result = register_account(email, password, os.environ, attempt=attempt)
            if result["status"] == "success":
                break

        # Append ke output
        with out_path.open("a") as f:
            f.write(json.dumps(result) + "\n")

        if result["status"] == "success":
            success += 1
            print(f"  ✓ SUCCESS")
        else:
            failed += 1
            print(f"  ✗ FAILED: {result.get('error')}")

        # Delay ke akun berikutnya (kecuali kalau ini yang terakhir)
        if i < len(pending):
            delay = random.randint(args.delay_min, args.delay_max)
            print(f"  → sleep {delay}s ({delay // 60}m{delay % 60}s) sebelum akun berikutnya...")
            time.sleep(delay)

    print("\n" + "=" * 60)
    print(f"Done. Success: {success}, Failed: {failed}, Total: {len(pending)}")
    print(f"Output: {out_path.absolute()}")
    print("=" * 60)


def _gen_password(length: int = 18) -> str:
    """Generate strong random password."""
    chars = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        "!@#$%^&*"
    )
    while True:
        pw = "".join(random.choices(chars, k=length))
        if (any(c.isupper() for c in pw)
                and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$%^&*" for c in pw)):
            return pw


if __name__ == "__main__":
    main()