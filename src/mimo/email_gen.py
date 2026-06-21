"""Email generation strategies untuk batch registration.

Tiga strategi utama:

1. **Gmail plus-aliasing** (`you+tag@gmail.com`)
   - Gratis, tanpa setup.
   - Semua forward ke 1 inbox Gmail.
   - ❌ Xiaomi mungkin filter/block email dengan `+`.

2. **Catch-all domain** (`random@yourdomain.com`)
   - Butuh domain + MX record diarahkan ke mail server (mis. ZohoMail free, ImprovMX, Cloudflare Email Routing).
   - ✅ Paling reliable untuk batch.
   - Bisa generate unlimited email random.

3. **Pre-defined list** (read dari file)
   - Anda sediakan list email sendiri (mis. dari Gmail lain, Outlook, Yandex, dll).
   - ✅ Cocok untuk testing dengan akun yang sudah Anda punya.
"""

import random
import re
import string
from pathlib import Path
from typing import Iterator


def random_tag(length: int = 8) -> str:
    """Random hex string untuk tag/prefix."""
    return "".join(random.choices(string.hexdigits.lower(), k=length))


# ── 1. Gmail Plus-Alias ─────────────────────────────────────────────────────
def gmail_plus_alias(base: str, tag: str | None = None) -> str:
    """Buat Gmail plus-alias.

    Contoh: base='fauzi@gmail.com' → 'fauzi+abc12345@gmail.com'
    Catatan: '+' mungkin di-block oleh Xiaomi — test dulu sebelum batch besar.
    """
    if "@" not in base:
        raise ValueError(f"base harus full email, dapat: {base!r}")
    local, domain = base.split("@", 1)
    if tag is None:
        tag = random_tag()
    return f"{local}+{tag}@{domain}"


def iter_gmail_plus_aliases(base: str, count: int) -> Iterator[str]:
    """Yield `count` unique Gmail plus-aliases."""
    seen = set()
    while len(seen) < count:
        e = gmail_plus_alias(base)
        if e not in seen:
            seen.add(e)
            yield e


# ── 2. Catch-all domain ─────────────────────────────────────────────────────
def catch_all(domain: str, prefix: str | None = None, length: int = 10) -> str:
    """Generate random email pada domain catch-all.

    Contoh: domain='mimo.kamu.com' → 'kfm9x2nq4p@mimo.kamu.com'
    Butuh: domain dengan MX record + catch-all forwarding ke 1 inbox.
    """
    if prefix:
        # Prefix + random suffix
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
        return f"{prefix}-{suffix}@{domain}"
    else:
        local = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
        return f"{local}@{domain}"


def iter_catch_all(domain: str, count: int, prefix: str | None = None) -> Iterator[str]:
    """Yield `count` unique catch-all emails."""
    seen = set()
    while len(seen) < count:
        e = catch_all(domain, prefix=prefix)
        if e not in seen:
            seen.add(e)
            yield e


# ── 3. Pre-defined list (read from file) ────────────────────────────────────
def iter_from_file(path: str | Path) -> Iterator[str]:
    """Yield emails dari file (satu per baris atau CSV).

    Format yang didukung:
      - Satu email per baris: foo@bar.com
      - CSV dengan header 'email': email\nfoo@bar.com\nbaz@bar.com
      - Dipisah koma: foo@bar.com,baz@bar.com
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File tidak ada: {path}")
    text = p.read_text()
    # Detect: contains newlines or commas?
    if "\n" in text:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("email"):
                continue
            for email in _split_emails(line):
                if email:
                    yield email
    else:
        for email in _split_emails(text):
            if email:
                yield email


def _split_emails(s: str) -> list[str]:
    """Split string by comma/semicolon, validate email format."""
    pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    parts = re.split(r"[,;\s]+", s.strip())
    return [p for p in parts if pattern.match(p)]


# ── 4. Unified generator ────────────────────────────────────────────────────
def generate_emails(
    count: int,
    strategy: str = "catch_all",
    *,
    base: str | None = None,
    domain: str | None = None,
    prefix: str | None = None,
    file: str | None = None,
) -> list[str]:
    """Top-level generator. Pilih strategi via `strategy`.

    Args:
        count: jumlah email
        strategy: 'catch_all' | 'gmail_plus' | 'from_file'
        base: Gmail base (untuk gmail_plus)
        domain: domain (untuk catch_all)
        prefix: optional prefix
        file: path (untuk from_file)
    """
    if strategy == "catch_all":
        if not domain:
            raise ValueError("`domain` required untuk strategy=catch_all")
        return list(iter_catch_all(domain, count, prefix=prefix))
    if strategy == "gmail_plus":
        if not base:
            raise ValueError("`base` required untuk strategy=gmail_plus")
        return list(iter_gmail_plus_aliases(base, count))
    if strategy == "from_file":
        if not file:
            raise ValueError("`file` required untuk strategy=from_file")
        return list(iter_from_file(file))[:count]
    raise ValueError(f"Unknown strategy: {strategy}")