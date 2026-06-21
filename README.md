# MiMo Register — Xiaomi Account Browserless Registration + MiMo Bot

100% HTTP API, **no browser**. Lengkap: register akun Xiaomi + apply MiMo UltraSpeed + refresh cookies.

Berdasarkan reverse-engineering dari HAR (`regis only account.xiaomi.com.har`) dan referensi [`guajiimi/xiaomi-register`](https://github.com/guajiimi/xiaomi-register).

## Flow (8 langkah)

```
1. GET register page (warm-up, collect cookies)
2. POST captcha/v2/data → e_token
3. POST CapSolver createTask (reCAPTCHA Enterprise + enterprisePayload)
4. POST captcha/v2/recaptcha/verify → vToken
5. Encrypt email+password (AES-128-CBC + RSA-PKCS1v15) → EUI + enc_params
6. POST sendEmailRegTicket (vToken as COOKIE, icode INTENTIONALLY EMPTY)
7. Read 6-digit OTP via IMAP
8. POST verifyEmailRegTicket → account created + passToken/serviceToken cookies
```

## Setup

```bash
# 1. Install dependencies
python3 -m pip install -e ".[dev]"

# 2. (optional) Install Node.js + crypto-js for encrypt.cjs fallback
npm install -g crypto-js

# 3. Copy env & fill credentials
cp .env.example .env
# Edit .env:
#   CAPSOLVER_API_KEY=your_key
#   EMAIL=email_baru@kamu.com
#   XIAOMI_PASSWORD=Strong!Pass2026
#   IMAP_USER=gmail@kamu.com
#   IMAP_PASS=gmail_app_password

# 4. Setup proxy (WAJIB untuk VPS — Xiaomi block data-center IP 503)
# Install WARP CLI atau residential proxy, set PROXY_URL=socks5://...
```

## Usage

```bash
# Register 1 akun
python -m mimo.register

# Batch — multiple akun sequential dengan rate limiting
python -m mimo.batch --count 5 --email-strategy catch_all --email-domain mimo.kamu.com

# MiMo bot: login → SSO → bind referral → apply UltraSpeed
python -m mimo.bot --account accounts.jsonl --row 0 --referral MX5V9X

# Refresh cookies akun existing (TTL pendek, jangan register ulang)
python -m mimo.refresh --all --account accounts.jsonl

# Test Cloudflare Email Routing setup
python -m mimo.setup_test

# Verify crypto implementation
python -m pytest tests/ -v

# Debug: encrypt field manual
python -m mimo.cli_encrypt '{"email":"test@gmail.com","password":"Test123!"}'

# Encrypt captcha payload
python -m mimo.cli_encrypt payload '{"type":0,"scene":"register","env":{"p11":123}}'
```

## MiMo Bot (login → apply UltraSpeed)

Setelah register akun, pakai `mimo.bot` untuk login & apply:

```bash
# Dari file JSONL (output register/batch)
python -m mimo.bot --account accounts.jsonl --row 0 --referral MX5V9X

# Manual
python -m mimo.bot --email user@example.com --password 'pwd' --referral MX5V9X \
  --name "Your Name" --phone "08123456789"

# Dry run (test tanpa side-effect)
python -m mimo.bot --account accounts.jsonl --referral MX5V9X --dry-run
```

Flow lengkap:
1. **Login** ke `account.xiaomi.com` (auto-solve captcha via CapSolver kalau perlu)
2. **SSO** ke `platform.xiaomimimo.com` via `/sts` callback
3. **Bind referral** (opsional) ke akun MiMo baru
4. **Apply UltraSpeed** beta (opsional, perlu form fields)

Required `.env`:
```ini
CAPSOLVER_API_KEY=...
PROXY_URL=socks5://127.0.0.1:40000
REFERRAL_CODE=MX5V9X
```

## Email list (strategi `from_file`)

Template: `email_list.example.txt` — copy & edit:

```bash
cp email_list.example.txt email_list.txt
# Edit — tambahkan email Anda
python -m mimo.batch --email-strategy from_file --email-file email_list.txt
```

Format didukung: satu per baris, CSV, atau dipisah koma/semicolon/whitespace.

## Cookie Refresh (re-login akun existing)

Cookie Xiaomi (passToken, serviceToken) TTL pendek. Daripada register ulang,
`mimo.refresh` login pakai email+password lalu update cookies di JSONL:

```bash
# Refresh 1 akun (row 0)
python -m mimo.refresh --account accounts.jsonl --row 0

# Refresh semua akun sukses
python -m mimo.refresh --all --account accounts.jsonl

# In-place update (default) atau output ke file lain
python -m mimo.refresh --all --account accounts.jsonl --out accounts_fresh.jsonl

# Skip validasi MiMo (lebih cepat, tidak hit /userProfile)
python -m mimo.refresh --all --account accounts.jsonl --no-validate

# Dry run
python -m mimo.refresh --all --account accounts.jsonl --dry-run
```

**Output JSONL schema** (per akun sukses di-refresh):

```json
{
  "email": "u@kamu.com",
  "password": "...",
  "status": "success",
  "cookies": { "passToken": "new_pt", "serviceToken": "new_st", ... },
  "passToken": "new_pt",
  "serviceToken": "new_st",
  "userId": "u1",
  "cUserId": "c1",
  "refreshed_at": "2026-06-21T05:30:00Z",
  "validated": true,
  "validation_profile": { "userId": "u1", "email": "u@kamu.com" }
}
```

**Status code:**
- `success` — login OK + validasi MiMo OK
- `unvalidated` — login OK tapi `/userProfile` gagal (cookies refresh, validasi kemudian)
- `failed` — login gagal (password salah, captcha, lock, dll)
- `dry_run` — mode simulasi

**Use cases:**
- Cookie TTL habis (~jam sampai hari)
- Periodic maintenance (`cron` harian)
- Refresh sebelum apply UltraSpeed baru

## Batch (banyak akun)

`mimo.batch` mendukung 3 strategi email + rate limiting:

### Strategi 1: Catch-all domain (RECOMMENDED untuk produksi)

Butuh domain + MX record + catch-all forwarding ke 1 inbox.
Paling simpel kalau DNS di **Cloudflare** — pakai [Cloudflare Email Routing](docs/cloudflare-setup.md) (free, built-in).

Panduan lengkap: **[docs/cloudflare-setup.md](docs/cloudflare-setup.md)**

Ringkasan 5 menit:
1. Cloudflare Dashboard → Email → Email Routing → Enable
2. Add destination: Gmail Anda → verify
3. Create catch-all rule: `*@domain.com` → forward ke Gmail
4. Setup Gmail App Password (https://myaccount.google.com/apppasswords)
5. Test: `python -m mimo.setup_test` (otomatis kirim test email + verify IMAP)

Setelah setup OK, jalankan batch:

```bash
python -m mimo.batch \
  --count 10 \
  --email-strategy catch_all \
  --email-domain mimo.kamu.com \
  --delay-min 300 --delay-max 900 \
  --out accounts.jsonl
```

### Strategi 2: Gmail plus-aliasing (gratis, no setup)

```bash
python -m mimo.batch \
  --count 5 \
  --email-strategy gmail_plus \
  --email-base kamu@gmail.com \
  --delay-min 300 --delay-max 600
```

⚠️ Xiaomi mungkin filter email dengan `+`. Test 1-2 akun dulu.

### Strategi 3: List dari file

```bash
# emails.txt:
# foo@gmail.com
# bar@outlook.com
# baz@yahoo.com

python -m mimo.batch \
  --email-strategy from_file \
  --email-file emails.txt \
  --out accounts.jsonl
```

### Rate limiting & safety

| Flag | Default | Rekomendasi |
|---|---|---|
| `--delay-min` | 300s (5min) | 600s (10min) untuk aman |
| `--delay-max` | 900s (15min) | 1200s (20min) |
| `--max-retries` | 2 | 2-3 |
| `--resume` | true | keep true (skip akun yg sudah sukses) |

Output JSONL (`accounts.jsonl`):

```json
{"email":"a@kamu.com","status":"success","cookies":{"passToken":"...","serviceToken":"..."},"created_at":"...","attempt":1}
{"email":"b@kamu.com","status":"failed","error":"RegisterError: captcha failed","failed_at":"...","attempt":3}
```

**Run bisa di-interrupt (Ctrl-C) dan di-resume** — file JSONL append-only, akun sukses di-skip otomatis.

## Output

File `xiaomi_account.json` setelah berhasil:

```json
{
  "email": "you@gmail.com",
  "password": "Strong!Pass2026",
  "cookies": {
    "passToken": "...",
    "serviceToken": "...",
    "userId": "...",
    "cUserId": "..."
  },
  "created_at": "2026-06-21T11:35:00Z"
}
```

## Crypto internals

- **AES-128-CBC + PKCS7** dengan IV tetap `0102030405060708`
- **AES key** random 16-char dari charset `A-Za-z0-9!@#$%^&*` (per request)
- **RSA-PKCS1v15** untuk `s` (captcha) dan EUI header
- 2 RSA public keys hard-coded di `src/mimo/crypto.py`
- `EUI = RSA(base64(aesKey)) + "." + base64("email,password")`

## Critical gotchas

| Gotcha | Detail |
|---|---|
| `icode` SENGAJA KOSONG | Captcha pass via cookie `vToken` |
| `vToken` sebagai COOKIE | Bukan body param! `domain=global.account.xiaomi.com` |
| `qs=%253Fsid%253Dpassport` | Double-encoded, jangan encode ulang |
| EUI field names HARUS `email,password` | Server validasi urutan |
| VPS IP di-block 503 | Pakai WARP / residential proxy |
| 30-50% captcha failure | Retry loop sampai 4× dengan e_token baru |
| Response `&&&START&&&` prefix | Strip sebelum JSON parse |
| IMAP body MIME soft-break | Hapus `=\r\n` sebelum regex OTP |
| `env.p33 = []` | JANGAN isi `["webdriver"]` (bot detected) |

## Struktur

```
.
├── README.md
├── PRD.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   └── cloudflare-setup.md      ← panduan setup catch-all email
├── email_list.example.txt        ← template untuk strategi from_file
├── captures/                     ← HAR + HTML asal (untuk referensi)
├── src/mimo/                     ← source code (package)
│   ├── __init__.py
│   ├── crypto.py                 ← AES + RSA helpers
│   ├── register.py               ← 8-step register flow + CapSolver
│   ├── batch.py                  ← batch orchestrator + rate limiting
│   ├── bot.py                    ← login Xiaomi + SSO MiMo + bind + UltraSpeed
│   ├── refresh.py                ← cookie refresh untuk akun existing
│   ├── email_gen.py              ← 3 strategi email generation
│   ├── setup_test.py             ← test Cloudflare Email Routing
│   ├── cli_encrypt.py            ← CLI debug tool
│   └── crypto/
│       ├── encrypt.cjs           ← Node.js EUI generator (optional)
│       └── payload_template.json ← captcha fingerprint template
└── tests/                        ← 79 unit tests
    ├── test_crypto.py
    ├── test_har_match.py
    ├── test_email_gen.py
    ├── test_batch.py
    ├── test_bot.py
    └── test_refresh.py
```

## CLI commands (6 entrypoints)

| Command | Fungsi |
|---|---|
| `mimo-register` | Daftar 1 akun Xiaomi (8-step flow) |
| `mimo-batch` | Daftar banyak akun sequential dengan rate limiting |
| `mimo-bot` | Login + SSO MiMo + bind referral + apply UltraSpeed |
| `mimo-refresh` | Refresh cookies akun existing (no register ulang) |
| `mimo-encrypt` | Encrypt field/payload manual (debug) |
| `mimo-setup-test` | Test Cloudflare Email Routing + IMAP |

Semua available sebagai console script setelah `pip install -e .`:
```bash
mimo-register --help
mimo-batch --help
mimo-bot --help
mimo-refresh --help
mimo-encrypt --help
mimo-setup-test
```

## Lisensi

Private use only. Hormati ToS Xiaomi.