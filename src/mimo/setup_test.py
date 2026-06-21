"""Test apakah Cloudflare Email Routing + Gmail IMAP setup sudah jalan.

Cara pakai:
    1. Set TEST_DOMAIN di .env (domain Cloudflare Anda)
    2. Set IMAP_USER & IMAP_PASS di .env (App Password Gmail)
    3. python -m mimo.setup_test
    4. Kirim email ke <random>@domain-anda.com dari mana saja
    5. Script akan cek apakah email masuk ke Gmail dalam 60 detik
"""

import os
import random
import smtplib
import string
import sys
import time
from email.mime.text import MIMEText

import imaplib
from dotenv import load_dotenv


def gen_random_tag(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def test_dns(domain: str) -> bool:
    """Cek MX record domain harus point ke cloudflare.net."""
    import subprocess
    try:
        result = subprocess.run(
            ["dig", "+short", "MX", domain],
            capture_output=True, text=True, timeout=10,
        )
        mx_records = result.stdout.strip()
        print(f"  MX records untuk {domain}:")
        for line in mx_records.splitlines():
            print(f"    {line}")
        if "cloudflare.net" in mx_records:
            print("  ✓ MX record point ke Cloudflare")
            return True
        print("  ✗ MX record BUKAN ke Cloudflare!")
        return False
    except FileNotFoundError:
        print("  ⚠ `dig` tidak tersedia. Install dengan `brew install bind` atau pakai nslookup.")
        return None


def send_test_email(domain: str, sender: str, sender_pass: str) -> str:
    """Kirim test email ke random@domain. Returns random tag."""
    tag = gen_random_tag()
    recipient = f"test-{tag}@{domain}"
    msg = MIMEText(f"Test catch-all — random tag: {tag}")
    msg["Subject"] = f"Cloudflare Routing Test {tag}"
    msg["From"] = sender
    msg["To"] = recipient

    print(f"  sending test email → {recipient}")
    # Coba Gmail SMTP
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, sender_pass)
            s.send_message(msg)
        print(f"  ✓ sent (random tag: {tag})")
        return tag
    except Exception as e:
        print(f"  ✗ SMTP failed: {e}")
        print(f"  fallback: kirim manual dari akun Gmail lain ke {recipient}")
        return tag


def poll_imap_for_tag(imap_user: str, imap_pass: str, tag: str,
                      timeout: int = 60) -> bool:
    """Poll IMAP untuk email dengan subject mengandung tag."""
    deadline = time.time() + timeout
    print(f"  polling IMAP untuk tag '{tag}' (timeout {timeout}s)...")
    while time.time() < deadline:
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            imap.login(imap_user, imap_pass)
            imap.select("INBOX")
            _, data = imap.search(None, f'(SUBJECT "Cloudflare Routing Test {tag}")')
            ids = data[0].split()
            if ids:
                print(f"  ✓ email ditemukan (ID: {ids[0]})")
                imap.logout()
                return True
            imap.logout()
        except Exception as e:
            print(f"  IMAP error: {e}")
        time.sleep(5)
        print(f"    ...{int(deadline - time.time())}s remaining")
    print(f"  ✗ email tidak ditemukan dalam {timeout}s")
    return False


def main():
    load_dotenv()
    domain = os.getenv("TEST_DOMAIN", "")
    imap_user = os.getenv("IMAP_USER", "")
    imap_pass = os.getenv("IMAP_PASS", "")
    sender_pass = os.getenv("TEST_SENDER_PASS", imap_pass)  # default: same as IMAP

    if not domain:
        print("Set TEST_DOMAIN=<yourdomain.com> di .env")
        sys.exit(1)
    if not imap_user or not imap_pass:
        print("Set IMAP_USER & IMAP_PASS di .env")
        sys.exit(1)

    print("=" * 60)
    print("Cloudflare Email Routing — Setup Test")
    print("=" * 60)
    print(f"Domain     : {domain}")
    print(f"Gmail (IMAP): {imap_user}")
    print()

    print("[1/3] DNS / MX records:")
    dns_ok = test_dns(domain)
    print()

    print("[2/3] Send test email:")
    tag = send_test_email(domain, imap_user, sender_pass)
    print()

    print("[3/3] Poll IMAP untuk verifikasi catch-all:")
    imap_ok = poll_imap_for_tag(imap_user, imap_pass, tag, timeout=60)
    print()

    print("=" * 60)
    if imap_ok:
        print("✓ Setup OK — Cloudflare Email Routing + Gmail IMAP berjalan!")
        print()
        print("Sekarang bisa jalankan:")
        print(f"  python -m mimo.batch --count 5 \\")
        print(f"    --email-strategy catch_all --email-domain {domain}")
    else:
        print("✗ Setup BELUM jalan. Troubleshoot:")
        print("  - Cek spam folder Gmail")
        print("  - Tunggu 5-10 menit (propagasi MX)")
        print("  - Cek Cloudflare dashboard → Email → Routes")
    print("=" * 60)
    sys.exit(0 if imap_ok else 1)


if __name__ == "__main__":
    main()