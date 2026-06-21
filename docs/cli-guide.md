# CLI Guide — mimo-register

> Dokumentasi lengkap tiap CLI command. Referensi cepat untuk `register`, `batch`, `bot`, `refresh`, `e2e`, `cf-setup`, `encrypt`, `setup-test`.

## Daftar Isi

1. [Ringkasan 8 CLI](#ringkasan-8-cli)
2. [Konvensi CLI](#konvensi-cli)
3. [Environment Variables](#environment-variables)
4. [Per-CLI Reference](#per-cli-reference)
   - [`mimo-register`](#mimo-register) — daftar 1 akun Xiaomi
   - [`mimo-batch`](#mimo-batch) — daftar banyak akun sequential
   - [`mimo-bot`](#mimo-bot) — login + SSO + bind + UltraSpeed
   - [`mimo-refresh`](#mimo-refresh) — refresh cookies expired
   - [`mimo-e2e`](#mimo-e2e) — **orchestrator end-to-end** (register → SSO → profile → API key)
   - [`mimo-bulk`](#mimo-bulk) — **bulk N akun sequential** (register → SSO → API key)
   - [`mimo-cf-setup`](#mimo-cf-setup) — setup Cloudflare Email Routing
   - [`mimo-encrypt`](#mimo-encrypt) — debug encrypt manual
   - [`mimo-setup-test`](#mimo-setup-test) — verify Cloudflare + IMAP
5. [Common Workflows](#common-workflows)
6. [File Output Reference](#file-output-reference)

---

## Ringkasan 8 CLI

| Command | Fungsi | Butuh CAPTCHA? | Butuh Proxy? |
|---|---|---|---|
| `mimo-register` | Daftar 1 akun Xiaomi | ✅ | ⚠ (VPS) |
| `mimo-batch` | Daftar banyak akun sequential | ✅ | ⚠ (VPS) |
| `mimo-bot` | Login Xiaomi + SSO MiMo + bind + UltraSpeed | ❌ (pakai cookies) | ⚠ (VPS) |
| `mimo-refresh` | Refresh cookies akun existing | ✅ (kalau expired) | ⚠ (VPS) |
| `mimo-e2e` | **register → SSO → profile → API key** (1 akun) | ✅ (kalau register) | ⚠ (VPS) |
| **`mimo-bulk`** | **bulk register → SSO → API key (N akun sequential)** | ✅ | ⚠ (VPS) |
| `mimo-cf-setup` | Setup Cloudflare Email Routing via API | ❌ | ❌ |
| `mimo-encrypt` | Debug: encrypt field/payload manual | ❌ | ❌ |
| `mimo-setup-test` | Verify Cloudflare Email Routing + IMAP | ❌ (SMTP only) | ❌ |

**CAPTCHA solver**: `CAPSOLVER_API_KEY` di `.env`
**Proxy**: `PROXY_URL` + `USE_PROXY=1` di `.env` (WAJIB untuk VPS — Xiaomi block data-center IP)

---

## Konvensi CLI

Semua CLI pakai pola:

```bash
mimo-<command> [OPTIONS]
```

**Help**:
```bash
mimo-<command> --help
```

**Common flags** (may vary per command):
- `--email EMAIL` — email untuk register baru
- `--password PASSWORD` — password untuk register
- `--account FILE` — path ke JSON file (existing account)
- `--row N` — row index di JSON file (default 0)
- `--dry-run` — simulasi tanpa eksekusi
- `--out FILE` — output file path

---

## Environment Variables

Lokasi: file `.env` di root project. Wajib di-set sebelum menjalankan CLI apapun.

### WAJIB

```ini
# CapSolver (https://dashboard.capsolver.com/)
CAPSOLVER_API_KEY=your_capsolver_api_key

# Xiaomi account (untuk register baru)
EMAIL=your_email@example.com
XIAOMI_PASSWORD=YourStrongPassword!2026

# IMAP (untuk baca OTP email)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_gmail@gmail.com
IMAP_PASS=your_gmail_app_password
```

### WAJIB untuk VPS

```ini
# Proxy (WARP SOCKS5 atau residential) — Xiaomi block IP VPS
USE_PROXY=1
PROXY_URL=socks5://127.0.0.1:40000
```

### OPSIONAL — Cloudflare (untuk catch-all email)

```ini
CF_API_TOKEN=your_cloudflare_api_token
CF_ZONE_ID=your_zone_id
CF_ACCOUNT_ID=your_account_id        # auto-detect kalau kosong
CF_DEST_EMAIL=email.anda@gmail.com
```

### OPSIONAL — Lainnya

```ini
REGION=ID                              # Xiaomi region
REFERRAL_CODE=MX5V9X                   # untuk mimo-bot
BATCH_EMAIL_STRATEGY=catch_all         # default untuk mimo-batch
TEST_DOMAIN=mimo.domain-anda.com       # untuk mimo-setup-test
TEST_SENDER_PASS=                      # kalau beda dari IMAP_PASS
```

> **Lihat juga**: `.env.example` di root project (template lengkap dengan comments).

---

## Per-CLI Reference

### `mimo-register`

Daftar 1 akun Xiaomi baru. Full 8-step flow.

**Usage**:
```bash
mimo-register
```

**Behavior**:
- Baca `EMAIL` + `XIAOMI_PASSWORD` dari `.env`
- Run 8 step: warmup → captcha → sendEmailRegTicket → IMAP OTP → verifyEmailRegTicket
- Output: `xiaomi_account.json` (single object)

**Contoh**:
```bash
# Default (pakai env vars)
mimo-register

# Override via env inline
EMAIL=new@domain.com XIAOMI_PASSWORD='P@ss!' mimo-register
```

**Output**:
- `xiaomi_account.json`:
  ```json
  {
    "email": "new@domain.com",
    "password": "P@ss!",
    "cookies": {"passToken": "...", "cUserId": "...", "userId": "..."},
    "created_at": "2026-06-21T..."
  }
  ```

**Catatan**:
- `mimo-register` **TIDAK save ke xiaomi_account.json** (lihat `mimo-e2e` untuk itu)
- Pakai `mimo-e2e` kalau mau end-to-end (register + SSO + profile)
- CAPTCHA: pakai CapSolver, butuh API key

---

### `mimo-batch`

Daftar banyak akun Xiaomi sequential dengan rate limiting.

**Usage**:
```bash
mimo-batch --count N --email-strategy STRATEGY [OPTIONS]
```

**Wajib**:
- `--count N` — jumlah akun (atau pakai `--emails`)
- `--email-strategy STRATEGY` — `catch_all` | `gmail_plus` | `from_file`

**Email strategies**:
- `catch_all` — generate random `<id>@yourdomain.com` (butuh catch-all setup)
- `gmail_plus` — generate `you+tag@gmail.com` (butuh Gmail)
- `from_file` — read dari `email_list.txt` (1 email per baris)

**Opsional**:
- `--email-domain DOMAIN` — untuk `catch_all` (mis. `mimo.kamu.com`)
- `--email-base EMAIL` — untuk `gmail_plus` (mis. `kamu@gmail.com`)
- `--email-prefix PREFIX` — optional, untuk `catch_all`
- `--email-file PATH` — untuk `from_file` (default: `email_list.txt`)
- `--emails e1@x.com e2@x.com ...` — list eksplisit (override strategi)
- `--password PWD` — password tunggal (atau random per akun)
- `--password-len N` — panjang random password (default 18)
- `--password-from-file PATH` — file dengan 1 password per baris (round-robin)
- `--delay-min S` — delay minimum antar akun, detik (default 300 = 5min)
- `--delay-max S` — delay maksimum antar akun, detik (default 900 = 15min)
- `--max-retries N` — retry per akun jika gagal (default 2)
- `--resume` / `--no-resume` — skip akun sukses (default: resume ON)
- `--out FILE` — output JSONL (default: `accounts.jsonl`)
- `--dry-run` — cetak plan tanpa eksekusi

**Contoh**:
```bash
# 5 akun via catch-all domain
mimo-batch --count 5 --email-strategy catch_all --email-domain mimo.kamu.com

# 3 akun via Gmail plus-aliasing
mimo-batch --count 3 --email-strategy gmail_plus --email-base kamu@gmail.com

# Akun dari file
mimo-batch --email-strategy from_file --email-file email_list.txt

# List email eksplisit
mimo-batch --emails a@x.com b@x.com --password 'Str0ngP@ss'

# Dry run — lihat plan tanpa register
mimo-batch --count 3 --email-strategy catch_all --email-domain mimo.kamu.com --dry-run
```

**Output**:
- `accounts.jsonl` (satu JSON per line):
  ```jsonl
  {"email":"...","password":"...","status":"success","cookies":{...},"created_at":"..."}
  {"email":"...","password":"...","status":"failed","error":"...","failed_at":"..."}
  ```

**Catatan**:
- Sequential (1 akun pada satu waktu) — JANGAN parallel
- Random delay 5-15 menit antar akun
- Resume-able: akun sukses di-skip kalau di-run ulang
- CAPTCHA: pakai CapSolver (rate limit: ~$0.003/solve)

---

### `mimo-bot`

Login Xiaomi → SSO ke MiMo platform → bind referral → apply UltraSpeed.

**Usage**:
```bash
mimo-bot --account FILE --row N [OPTIONS]
# atau
mimo-bot --email EMAIL --password PASSWORD [OPTIONS]
```

**Wajib** (salah satu):
- `--account FILE` — JSON file dengan akun existing
- `--email EMAIL` + `--password PASSWORD` — credentials langsung

**Opsional**:
- `--row N` — row index di file (default 0)
- `--referral CODE` — referral code (default: dari env `REFERRAL_CODE`)
- `--name NAME` — nama untuk form UltraSpeed
- `--phone PHONE` — phone untuk form UltraSpeed
- `--company COMPANY` — company
- `--industry INDUSTRY` — industry
- `--scenario SCENARIO` — usage scenario
- `--additional-info TEXT` — additional info
- `--dry-run` — simulasi

**Contoh**:
```bash
# Dari JSON file
mimo-bot --account xiaomi_account.json --row 0 --referral MX5V9X

# Manual
mimo-bot --email user@x.com --password 'pwd' --referral MX5V9X

# Dry run
mimo-bot --account accounts.jsonl --row 0 --referral MX5V9X --dry-run
```

**Output** (stdout):
```
[▸] Login: user@x.com
[*] CapSolver: configured
[*] Proxy: socks5://127.0.0.1:40000
[▸] SSO → MiMo platform...
[▸] Bind referral: MX5V9X
[▸] Apply UltraSpeed beta...
─────────────────────────────────────
Status: SUCCESS
  ✓ login
  ✓ sso
  ✓ referral
  ✓ ultraspeed
─────────────────────────────────────
```

**Catatan**:
- `bind_referral` saat ini **perlu phone verification** di MiMo → kemungkinan return 401
- `apply_ultraspeed` mungkin return 401 (known issue, non-fatal)
- Kalau pakai `--account`, login pakai passToken existing (skip captcha)

---

### `mimo-refresh`

Refresh cookies untuk akun Xiaomi existing (kalau TTL sudah expire).

**Usage**:
```bash
mimo-refresh --account FILE --row N [OPTIONS]
mimo-refresh --all --account FILE [OPTIONS]
```

**Wajib**:
- `--account FILE` — JSON file dengan akun existing
- Salah satu: `--row N` (1 akun) atau `--all` (semua)

**Opsional**:
- `--out FILE` — output file (default: in-place)
- `--in-place` — update file langsung (default kalau `--out` tidak diset)
- `--no-validate` — skip validasi MiMo `/userProfile` (lebih cepat)
- `--delay S` — delay antar akun (detik, default 0)

**Contoh**:
```bash
# Refresh 1 akun
mimo-refresh --account xiaomi_account.json --row 0

# Refresh semua akun sukses
mimo-refresh --all --account accounts.jsonl

# Save ke file lain
mimo-refresh --all --account accounts.jsonl --out accounts_fresh.jsonl

# Skip validasi
mimo-refresh --all --account accounts.jsonl --no-validate
```

**Output**:
- Update JSON/JSONL file dengan `refreshed_at` timestamp
- `status: success` (cookies OK + validate OK) atau `unvalidated` (cookies OK tapi validate fail)
- `status: failed` (login gagal)

**Catatan**:
- Pakai credentials dari file (email + password) untuk login ulang
- CAPTCHA mungkin diperlukan untuk login (jika IP reputation rendah)
- Validasi default: GET MiMo `/userProfile` untuk verify cookies

---

### `mimo-e2e`

**Orchestrator end-to-end**: register akun → SSO MiMo → load profile → create API key.

**Usage**:
```bash
mimo-e2e --email EMAIL --password PASSWORD [OPTIONS]
# atau
mimo-e2e --account FILE --row N [OPTIONS]
```

**Wajib** (salah satu):
- `--email EMAIL` + `--password PASSWORD` — register akun baru
- `--account FILE` — JSON file dengan akun existing (skip register)

**Opsional**:
- `--row N` — row index di file (default 0)
- `--api-key-name NAME` — create API key dengan nama ini setelah SSO
- `--list-api-keys` — list existing API keys (skip create)

**Contoh**:
```bash
# Register + SSO + profile (no API key)
mimo-e2e --email baru@domain.com --password 'Str0ngP@ss!'

# Full pipeline: register + SSO + create API key
mimo-e2e --email baru@domain.com --password 'Str0ngP@ss!' --api-key-name "my-app"

# Pakai existing + list API keys
mimo-e2e --account xiaomi_account.json --list-api-keys

# Pakai existing + create new API key
mimo-e2e --account xiaomi_account.json --api-key-name "key-v2"
```

**Output**:
- `xiaomi_account.json` (array, append mode) — semua akun + cookies + profile + api_key
- `accounts.txt` (pipe-separated `email|password|apiKey`) — flat list
- `mimo_api_key.json` (single, mode 600) — kalau create API key

**Catatan**:
- **Paling sering dipakai** untuk full pipeline
- 5 step dalam 1 command:
  1. Register akun baru (atau pakai existing)
  2. Login pakai passToken (skip captcha)
  3. SSO ke platform.xiaomimimo.com
  4. GET `/api/v1/userProfile` (verify session)
  5. (Optional) POST `/api/v1/apiKeys` (create API key)
- `bind_referral` OFF (perlu phone verification, di-skip)

---

### `mimo-e2e`

**Orchestrator end-to-end**: register akun → SSO MiMo → load profile → create API key.

**Usage**:
```bash
mimo-e2e --email EMAIL --password PASSWORD [OPTIONS]
# atau
mimo-e2e --account FILE --row N [OPTIONS]
```

**Wajib** (salah satu):
- `--email EMAIL` + `--password PASSWORD` — register akun baru
- `--account FILE` — JSON file dengan akun existing (skip register)

**Opsional**:
- `--row N` — row index di file (default 0)
- `--api-key-name NAME` — create API key dengan nama ini setelah SSO
- `--list-api-keys` — list existing API keys (skip create)
- `--no-api-key` — skip API key creation

**Contoh**:
```bash
# Register + SSO + profile (no API key)
mimo-e2e --email baru@domain.com --password 'Str0ngP@ss!'

# Full pipeline: register + SSO + create API key
mimo-e2e --email baru@domain.com --password 'Str0ngP@ss!' --api-key-name "my-app"

# Pakai existing + list API keys
mimo-e2e --account xiaomi_account.json --list-api-keys

# Pakai existing + create new API key
mimo-e2e --account xiaomi_account.json --api-key-name "key-v2"

# Zero-args — pakai defaults dari env (EMAIL_DOMAIN, XIAOMI_PASSWORD, API_KEY_NAME)
mimo-e2e
```

**Output**:
- `xiaomi_account.json` (array, append mode) — semua akun + cookies + profile + api_key
- `accounts.txt` (pipe-separated `email|password|apiKey`) — flat list
- `mimo_api_key.json` (single, mode 600) — kalau create API key

**Catatan**:
- **Paling sering dipakai** untuk full pipeline per akun
- 5 step dalam 1 command:
  1. Register akun baru (atau pakai existing)
  2. Login pakai passToken (skip captcha)
  3. SSO ke platform.xiaomimimo.com
  4. GET `/api/v1/userProfile` (verify session)
  5. Check agreement → kalau true, POST `/api/v1/apiKeys` (create API key)
- `bind_referral` OFF (perlu phone verification, di-skip)
- **Env defaults** (zero-args): EMAIL → auto-generate dari EMAIL_DOMAIN, password → XIAOMI_PASSWORD, api-key → API_KEY_NAME

---

### `mimo-bulk`

**Bulk end-to-end orchestrator**: register → SSO → API key untuk **banyak akun sequential**. Mirip `mimo-e2e` tapi untuk N akun dengan rate limiting.

**Usage**:
```bash
mimo-bulk --count N [OPTIONS]
# atau
mimo-bulk --from-jsonl FILE [OPTIONS]
```

**Wajib** (salah satu):
- `--count N` — jumlah akun yang akan dibuat
- `--from-jsonl FILE` — path ke JSONL file dengan email list (output `mimo-batch`)

**Opsional**:
- `--email-domain DOMAIN` — domain untuk generate email (default: dari `EMAIL_DOMAIN` env)
- `--api-key-name NAME` — nama API key (default: dari `API_KEY_NAME` env, atau `bulk-key`)
- `--password PWD` — password untuk semua akun (default: `XIAOMI_PASSWORD` env, atau random per akun)
- `--delay-min S` — delay minimum antar akun, detik (default 300 = 5min)
- `--delay-max S` — delay maksimum antar akun, detik (default 900 = 15min)
- `--out FILE` — output JSON file (default: `xiaomi_account.json`)
- `--dry-run` — cetak plan tanpa eksekusi

**Contoh**:
```bash
# Zero args — pakai env defaults (EMAIL_DOMAIN, XIAOMI_PASSWORD, API_KEY_NAME)
mimo-bulk

# 5 akun dengan default delay (5-15 min antar akun)
mimo-bulk --count 5

# Custom domain + delay
mimo-bulk --count 10 --email-domain other-domain.com --delay-min 600 --delay-max 1200

# Process existing JSONL (output dari mimo-batch)
mimo-bulk --from-jsonl accounts.jsonl

# Pakai custom API key name
mimo-bulk --count 3 --api-key-name "production-v1"

# Dry run — lihat plan tanpa register
mimo-bulk --count 3 --dry-run
```

**Output**:
- `xiaomi_account.json` (array, append mode) — semua akun
- `accounts.txt` (pipe-separated) — flat list email|password|apiKey
- `mimo_api_key.json` (mode 600) — last API key
- `bulk_run.log.jsonl` — per-account run log (status, error, timestamps)

**Resume mode**: kalau file `xiaomi_account.json` sudah ada, akun yang sudah punya `api_key` di-skip. Re-run aman untuk lanjut.

**Catatan**:
- **Paling cocok untuk produksi** (10-100 akun)
- Sequential 1 akun pada satu waktu — JANGAN parallel
- Random delay 5-15 menit (default) — anti rate limit Xiaomi
- Random password per akun (kalau `XIAOMI_PASSWORD` env kosong) — disimpan ke output
- CAPTCHA: pakai CapSolver per akun (~$0.003/solve)
- Per akun ~3-5 menit (register + SSO + API key), + delay 5-15 menit → 8-20 menit per akun
- Untuk 10 akun: ~80-200 menit (~1.5-3.5 jam)

---

### `mimo-cf-setup`

Setup Cloudflare Email Routing via API (catch-all forwarding).

**Usage**:
```bash
mimo-cf-setup --token TOKEN --domain DOMAIN --dest EMAIL [ACTION]
```

**Wajib** (untuk setup):
- `--token TOKEN` — Cloudflare API token (atau env `CF_API_TOKEN`)
- `--domain DOMAIN` — domain (untuk lookup Zone ID)
- `--dest EMAIL` — destination email (Gmail tujuan forward)

**Opsional**:
- `--zone-id ID` — Zone ID (skip `--domain` lookup)
- `--account-id ID` — Account ID (auto-detect)
- `--api-key KEY` + `--email EMAIL` — Global API Key mode (alternatif)
- `--dry-run` — simulasi

**Action** (salah satu):
- `--setup` (default) — setup catch-all rule
- `--status` — cetak status rules & destinations
- `--list-zones` — cetak semua zone di account

**Contoh**:
```bash
# Setup catch-all (idempotent — self-healing kalau rule salah)
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com

# Status check
mimo-cf-setup --token $CF_API_TOKEN --zone-id $CF_ZONE_ID --status

# List zones
mimo-cf-setup --token $CF_API_TOKEN --list-zones

# Pakai Global API Key (alternative)
mimo-cf-setup --api-key $CF_API_KEY --email $CF_EMAIL --domain mimo.kamu.com --dest gmail.anda@gmail.com

# Dry run
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com --dry-run
```

**Output** (setup):
```
[*] auth mode: token
[*] setup catch-all → gmail.anda@gmail.com
  ✓ destination already exists: gmail.anda@gmail.com (verified=True)
  ✓ catch-all rule sudah ada: id=abc123
✓ Setup selesai.
  Test: kirim email ke test123@mimo.kamu.com
  Verifikasi via: python -m mimo.setup-test
```

**Catatan**:
- Butuh API token dengan permission:
  - Account: **Email Routing Addresses:Edit**
  - Zone: **Email Routing Rules:Edit** (scoped ke domain)
  - Zone: **Zone:Read**
- Self-healing: kalau rule ada tapi disabled/drop, akan di-fix atau replace
- Setelah setup, run `mimo-setup-test` untuk verify

---

### `mimo-encrypt`

Debug: encrypt field/payload manual (untuk inspect output).

**Usage**:
```bash
mimo-encrypt '<JSON>'
mimo-encrypt payload '<JSON>'
```

**Contoh**:
```bash
# Encrypt email+password → EUI + encryptedParams
mimo-encrypt '{"email":"test@gmail.com","password":"P@ss123!"}'

# Output:
# {
#   "EUI": "<RSA-encrypted-aesKey>.<base64-field-names>",
#   "encryptedParams": {
#     "email": "<base64-AES-ciphertext>",
#     "password": "<base64-AES-ciphertext>"
#   }
# }

# Encrypt captcha fingerprint → s/d
mimo-encrypt payload '{"type":0,"scene":"register","env":{},"nonce":{"t":0,"r":0}}'

# Output:
# {
#   "s": "<RSA-encrypted-aesKey>",
#   "d": "<base64-AES-ciphertext>"
# }
```

**Catatan**:
- Hanya untuk debugging / verifikasi crypto
- Tidak melakukan request ke server
- Output identik dengan apa yang dikirim ke Xiaomi (asal KEY_CHARS + IV sama)

---

### `mimo-setup-test`

Verify Cloudflare Email Routing + Gmail IMAP end-to-end.

**Usage**:
```bash
mimo-setup-test
```

**Behavior**:
1. **DNS check**: verify MX record domain → cloudflare.net
2. **Send test email**: SMTP via Gmail ke `<random>@yourdomain.com`
3. **Poll IMAP**: cek inbox + Spam untuk test email
4. **Status**: ✓ kalau semua OK

**Required env vars**:
```ini
TEST_DOMAIN=mimo.kamu.com
IMAP_USER=gmail.anda@gmail.com
IMAP_PASS=your_gmail_app_password
TEST_SENDER_PASS=                # default: same as IMAP_PASS
```

**Contoh**:
```bash
# Full test (semua 3 step)
mimo-setup-test

# Test output:
# ============================================================
# Cloudflare Email Routing — Setup Test
# ============================================================
# Domain     : mimo.kamu.com
# Gmail (IMAP): gmail.anda@gmail.com
#
# [1/3] DNS / MX records:
#   ✓ MX record point ke Cloudflare
# [2/3] Send test email:
#   ✓ sent (random tag: abc12345)
# [3/3] Poll IMAP untuk verifikasi catch-all:
#   ✓ email ditemukan di INBOX
#
# ✓ Setup OK — Cloudflare Email Routing + Gmail IMAP berjalan!
```

**Catatan**:
- DNS check menggunakan `dig` (Linux/macOS dengan bind installed)
- Fallback ke `socket.getaddrinfo` kalau `dig` tidak ada
- Polling timeout: 60 detik (default)
- IMAP polling: cek INBOX + `[Gmail]/Spam`

---

## Common Workflows

### Workflow 1: First-time setup (sekali)

```bash
# 1. Setup Cloudflare catch-all email routing
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com

# 2. Verify setup
mimo-setup-test

# 3. Done — siap untuk register akun
```

### Workflow 2: Daftar 1 akun (test)

```bash
# Register + SSO + profile (no API key)
mimo-e2e --email test1@mimo.kamu.com --password 'Str0ngP@ss!'
```

### Workflow 3: Daftar banyak akun (production)

```bash
# Setup 5 akun
mimo-batch --count 5 --email-strategy catch_all --email-domain mimo.kamu.com \
  --delay-min 300 --delay-max 900 --out accounts.jsonl

# Lihat progress (file ter-update real-time)
tail -f accounts.jsonl
```

### Workflow 4: Apply MiMo features (per akun)

```bash
# Login + SSO + bind + UltraSpeed
mimo-bot --account accounts.jsonl --row 0 --referral MX5V9X

# Atau pakai existing
mimo-bot --account xiaomi_account.json --row 0 --referral MX5V9X
```

### Workflow 5: Refresh cookies (maintenance)

```bash
# Refresh 1 akun
mimo-refresh --account accounts.jsonl --row 0

# Refresh semua + save ke file baru
mimo-refresh --all --account accounts.jsonl --out accounts_fresh.jsonl
```

### Workflow 6: Full pipeline (register + SSO + API key)

```bash
# Register akun baru + langsung create API key
mimo-e2e --email baru@mimo.kamu.com --password 'Str0ngP@ss!' \
  --api-key-name "production-key-v1"

# Output:
#   ✓ xiaomi_account.json (1 akun)
#   ✓ accounts.txt (1 line: email|password|apiKey)
#   ✓ mimo_api_key.json (mode 600)
```

### Workflow 7: Bulk register + bulk apply

```bash
# Step 1: Register 10 akun
mimo-batch --count 10 --email-strategy catch_all --email-domain mimo.kamu.com --out accounts.jsonl

# Step 2: Apply UltraSpeed untuk setiap akun (sequential)
for i in 0 1 2 3 4 5 6 7 8 9; do
  mimo-bot --account accounts.jsonl --row $i --referral MX5V9X
  sleep 60
done
```

---

## File Output Reference

| File | Format | Created by | Contains |
|---|---|---|---|
| `xiaomi_account.json` | JSON array | `mimo-e2e` | email, password, cookies, profile, api_key per akun |
| `accounts.jsonl` | JSONL | `mimo-batch` | 1 JSON per line per akun (success/failed) |
| `accounts.txt` | Plain text | `mimo-e2e` | `email\|password\|apiKey` per line |
| `mimo_api_key.json` | JSON object | `mimo-e2e --api-key-name` | full API key (mode 600) |
| `email_list.txt` | Plain text | user | 1 email per line (untuk `--email-strategy from_file`) |

### `xiaomi_account.json` schema

```json
[
  {
    "email": "user@domain.com",
    "password": "Str0ngP@ss!",
    "cookies": {
      "passToken": "V1:...",
      "cUserId": "-cnl2p-...",
      "userId": "6878963297"
    },
    "profile": {
      "userId": "6878963297",
      "email": "u***r@d***n.com",
      "phone": null,
      "agreement": true,
      "idc": 311
    },
    "api_key": {
      "id": 3210512,
      "apiKeyName": "prod-key",
      "apiKey": "sk-...",
      "redactedApiKey": "sk-...****...",
      "createTime": "2026-06-21T..."
    } | null,
    "created_at": "2026-06-21T..."
  }
]
```

### `accounts.txt` format

```
user1@mimo.kamu.com|Str0ngP@ss1|sk-abc123...
user2@mimo.kamu.com|Str0ngP@ss2|sk-def456...
user3@mimo.kamu.com|Str0ngP@ss3|
```

(Field 3 = apiKey, kosong kalau `--list-api-keys` atau tanpa `--api-key-name`)

---

## Troubleshooting

### "Email address invalid" (code 88205)

Xiaomi reject email yang baru dipakai. **Tunggu 5-10 menit** atau coba email berbeda.

### "Callback连接不合法" (code 10025)

Biasanya solved otomatis. Kalau muncul, update `mimo-bot` ke versi terbaru (sudah pakai `callback=""`).

### "Captcha verify failed" / "System error 500"

Retry otomatis sampai 4×. Kalau terus gagal, coba:
- Tunggu 30 menit (rate limit CapSolver)
- Ganti IP / proxy
- Cek CapSolver balance

### VPS IP di-block 503

Set `USE_PROXY=1` + `PROXY_URL=socks5://127.0.0.1:40000` di `.env`. Install WARP CLI untuk proxy SOCKS5.

### Xiaomi reject dengan 87001 / 70016

Captcha token expired. Re-run otomatis akan retry dengan e_token baru.

### bind_referral return 401

MiMo butuh **phone verification** dulu. Skip bind/UltraSpeed untuk saat ini, atau tambah phone binding flow.

---

## Quick Reference Card

```bash
# Setup (sekali)
mimo-cf-setup --token $CF_API_TOKEN --domain DOMAIN --dest GMAIL
mimo-setup-test

# Single akun
mimo-e2e --email X --password Y

# Bulk akun
mimo-batch --count N --email-strategy catch_all --email-domain DOMAIN

# Bulk akun (full pipeline per akun)
mimo-bulk                                # pakai env defaults
mimo-bulk --count 5                      # 5 akun
mimo-bulk --from-jsonl accounts.jsonl   # process existing emails

# Apply MiMo features
mimo-bot --account FILE --row 0 --referral CODE

# Refresh cookies
mimo-refresh --all --account FILE

# Debug
mimo-encrypt '{"email":"x","password":"y"}'
mimo-encrypt payload '{"type":0,"scene":"register"}'
```

---

## Related Docs

- **`PRD.md`** — Product Requirements Document
- **`README.md`** — Project overview + quick start
- **`docs/cloudflare-setup.md`** — Panduan setup Cloudflare Email Routing (manual, tanpa API)
- **`docs/cloudflare-api-setup.md`** — Panduan API token Cloudflare + permission